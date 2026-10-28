"""稀疏包注册：生成 AppxManifest 与图标，注册/注销 Windows 分享入口。

为什么需要稀疏 MSIX：未打包的普通 exe 无权声明 Share Target。稀疏包
（AllowExternalContent）只注册包身份与扩展点，exe 留在原地，不进 WindowsApps。

两个从上游项目继承的实测结论，缺一不可：
1. 显示名绝不能含连续的「微信 / WeChat / Weixin」子串——微信宿主会把
   这类分享目标从转发菜单里隐藏掉。因此本包使用「聊天转发桥」；
2. 分享声明必须宽（SupportsAnyFileType + 多 DataFormat）：窄声明会被
   微信在枚举阶段直接剔除，运行时再自行校验只收 zip。

注册方式（真机验证）：把清单、图标与入口 exe 副本组装进布局目录
``<venv>/wcb4py-share``，用 ``Add-AppxPackage -Register`` 直接注册
（需开发者模式）。曾尝试「清单 + -ExternalLocation 指向 venv」免复制的
路线，实测被 0x80073D2E 拒绝：未签名包不能携带外部位置声明直接注册，
该路线只有签名 MSIX 才可行（上游 AppModelSamples issue #25 同结论）。
代价：重新安装/升级本包后需重新执行 register 刷新被复制的 exe。
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

PACKAGE_NAME = "WeChatBridge4Py.ShareTarget"
PUBLISHER = "CN=WeChatBridge4Py"
DISPLAY_NAME = "聊天转发桥"
PACKAGE_VERSION = "0.1.0.0"
APP_ID = "Share.Relay"
SHARE_EXE_NAME = "wcb4py-share.exe"

_ASSET_44 = "Square44x44Logo.png"
_ASSET_150 = "Square150x150Logo.png"


class RegistrationError(RuntimeError):
    """注册/注销失败。消息面向开发者，可直接展示。"""


def layout_dir() -> Path:
    """清单与图标（以及策略 B 的 exe 副本）所在的目录：<venv>/wcb4py-share。"""
    return Path(sys.prefix) / "wcb4py-share"


def share_entry_exe() -> Path:
    """定位分享入口 exe（pip/uv 安装时由 gui_scripts 生成的启动器）。

    Raises:
        RegistrationError: 找不到 exe，提示先安装本包（uv sync / pip install）。
    """
    override = os.environ.get("WCB4PY_SHARE_EXE")
    if override:
        candidate = Path(override)
        if not candidate.exists():
            raise RegistrationError(f"WCB4PY_SHARE_EXE 指向的文件不存在：{candidate}")
        return candidate
    scripts = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    candidate = scripts / SHARE_EXE_NAME
    if not candidate.exists():
        raise RegistrationError(
            f"未找到分享入口 {candidate}。请先在目标虚拟环境里安装本包"
            "（uv sync / uv pip install -e .）再执行 register。"
        )
    return candidate


def build_manifest(
    *,
    executable_rel: str,
    assets_rel: str,
    display_name: str = DISPLAY_NAME,
    version: str = PACKAGE_VERSION,
) -> str:
    """生成 AppxManifest.xml 文本（布局目录直注形态，无外部位置声明）。

    Args:
        executable_rel: Application/Executable 相对布局目录的路径。
        assets_rel: 图标目录的相对路径前缀（如 ``Assets``）。
        display_name: 分享面板展示名（不得含连续 微信/WeChat/Weixin 子串）。
        version: 包版本（四段式）。
    """
    logo44 = f"{assets_rel}\\{_ASSET_44}"
    logo150 = f"{assets_rel}\\{_ASSET_150}"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap uap10 rescap">
  <Identity Name="{PACKAGE_NAME}" Publisher="{PUBLISHER}" Version="{version}" ProcessorArchitecture="neutral" />
  <Properties>
    <DisplayName>{display_name}</DisplayName>
    <PublisherDisplayName>WeChatBridge4Py</PublisherDisplayName>
    <Logo>{logo150}</Logo>
  </Properties>
  <Resources>
    <Resource Language="zh-cn" />
    <Resource Language="en-us" />
  </Resources>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
    <rescap:Capability Name="unvirtualizedResources" />
  </Capabilities>
  <!--
    显示名「{display_name}」刻意不含连续的 微信/WeChat/Weixin 子串：
    微信宿主会隐藏带这些子串的分享目标（上游项目实测结论）。
  -->
  <Applications>
    <Application Id="{APP_ID}" Executable="{executable_rel}" uap10:TrustLevel="mediumIL" uap10:RuntimeBehavior="win32App">
      <uap:VisualElements AppListEntry="none" DisplayName="{display_name}" Description="把聊天合并转发归档交给你的 Python 程序" BackgroundColor="transparent" Square44x44Logo="{logo44}" Square150x150Logo="{logo150}" />
      <Extensions>
        <uap:Extension Category="windows.shareTarget">
          <!--
            宽声明只为枚举可见性：窄 SupportedFileTypes / 单一 DataFormat
            会被微信在枚举阶段剔除。真实负载由入口进程运行时校验（只收 zip）。
          -->
          <uap:ShareTarget Description="{display_name}">
            <uap:SupportedFileTypes><uap:SupportsAnyFileType /></uap:SupportedFileTypes>
            <uap:DataFormat>StorageItems</uap:DataFormat>
            <uap:DataFormat>Text</uap:DataFormat>
            <uap:DataFormat>URI</uap:DataFormat>
            <uap:DataFormat>Bitmap</uap:DataFormat>
          </uap:ShareTarget>
        </uap:Extension>
      </Extensions>
    </Application>
  </Applications>
</Package>
"""


def write_png(path: Path, size: int) -> None:
    """生成极简品牌图标（纯标准库：实底 + 桥形图样），避免引入图像依赖。

    Args:
        path: 输出 png 路径（父目录自动创建）。
        size: 正方形边长（像素）。
    """
    base = (24, 111, 99, 255)     # 深青底
    accent = (255, 255, 255, 255)  # 白色桥形

    def pixel(fx: float, fy: float) -> tuple[int, int, int, int]:
        top_bar = 0.16 <= fy <= 0.26
        bottom_bar = 0.74 <= fy <= 0.84
        pillar = (0.34 <= fx <= 0.42 or 0.58 <= fx <= 0.66) and 0.16 <= fy <= 0.84
        deck = 0.46 <= fy <= 0.54
        return accent if (top_bar or bottom_bar or pillar or deck) else base

    rows = b"".join(
        b"\x00"
        + bytes(
            value
            for x in range(size)
            for value in pixel(x / size, y / size)
        )
        for y in range(size)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def register() -> str:
    """注册分享入口（幂等：已注册则先注销再注册）。

    Returns:
        固定返回 "layout-copy"（当前唯一且经真机验证的策略）。

    Raises:
        RegistrationError: 注册失败；错误输出包含开发者模式等指引。
    """
    if os.name != "nt":
        raise RegistrationError("register 仅支持 Windows。")
    exe = share_entry_exe()
    target = layout_dir()
    _ensure_assets(target / "Assets")
    if installed_package():
        unregister()
    shutil.copyfile(exe, target / SHARE_EXE_NAME)
    manifest_path = target / "AppxManifest.xml"
    manifest_path.write_text(
        build_manifest(executable_rel=SHARE_EXE_NAME, assets_rel="Assets"),
        encoding="utf-8",
    )
    code, output = _run_powershell(["Add-AppxPackage", "-Register", str(manifest_path)])
    if code == 0 and installed_package():
        _note_strategy("layout-copy")
        return "layout-copy"
    raise RegistrationError(
        f"注册失败：exit={code} {output.strip()[:600]}"
        + _dev_mode_hint(output)
    )


def unregister() -> bool:
    """注销分享入口（未注册时静默返回 False）。"""
    if os.name != "nt":
        raise RegistrationError("unregister 仅支持 Windows。")
    code, output = _run_powershell([
        "Get-AppxPackage", "-Name", PACKAGE_NAME, "|", "Remove-AppxPackage"
    ])
    registered = installed_package() is not None
    if registered:
        raise RegistrationError(f"注销失败：{output.strip()[:400]}")
    return code == 0 and not registered


def installed_package() -> dict | None:
    """查询本机已注册的分享包；返回 {"name","fullName","version"} 或 None。"""
    if os.name != "nt":
        return None
    try:
        from winrt.windows.management.deployment import PackageManager

        manager = PackageManager()
        for package in manager.find_packages_for_user(""):
            package_id = package.id
            if package_id.name == PACKAGE_NAME:
                version = package_id.version
                return {
                    "name": package_id.name,
                    "fullName": str(package_id.full_name),
                    "version": f"{version.major}.{version.minor}.{version.build}.{version.revision}",
                }
        return None
    except Exception:
        code, output = _run_powershell([
            "Get-AppxPackage", "-Name", PACKAGE_NAME,
            "|", "Select-Object", "-Property", "Name,FullName,Version",
            "|", "ConvertTo-Json", "-Compress"
        ])
        if code != 0 or not output.strip() or output.strip() == "null":
            return None
        try:
            payload = json.loads(output)
            if isinstance(payload, list):
                payload = payload[0] if payload else None
            return payload if payload else None
        except ValueError:
            return None


def dev_mode_enabled() -> bool:
    """开发者模式（免签名 -Register 的前提）是否开启。"""
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock",
        ) as key:
            return winreg.QueryValueEx(key, "AllowDevelopmentWithoutDevLicense")[0] == 1
    except OSError:
        return False


def doctor() -> list[str]:
    """环境体检，返回逐条检查结果文本（同时直接打印交给 CLI 使用）。"""
    if os.name != "nt":
        return ["当前平台不是 Windows：本命令检查的内容仅对 win32 有意义。"]
    lines: list[str] = []
    lines.append(f"Python {sys.version.split()[0]}  prefix={sys.prefix}")
    try:
        exe = share_entry_exe()
        lines.append(f"[OK] 分享入口 exe：{exe}")
    except RegistrationError as error:
        lines.append(f"[缺失] {error}")
    package = installed_package()
    if package:
        lines.append(f"[OK] 已注册：{package['fullName']}（{package['version']}）")
    else:
        lines.append("[未注册] 尚未注册分享入口，运行 wcb4py register。")
    lines.append(
        "[OK] 开发者模式：已开启（免签名注册的前提）"
        if dev_mode_enabled()
        else "[未开启] 开发者模式未开启；注册前请以管理员运行：\n"
             "  reg add HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock"
             " /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f"
    )
    from wechatbridge4py.core.inbox import InboxPaths

    paths = InboxPaths()
    try:
        paths.ensure_created()
        lines.append(f"[OK] 收件箱：{paths.root}")
    except OSError as error:
        lines.append(f"[异常] 收件箱不可用：{error}")
    lines.append(
        "验证指引：微信里多选消息 → 合并转发 → 转发到其他应用 → 选「聊天转发桥」。\n"
        "若微信菜单里看不到入口：先用资源管理器选中 zip → 右键「共享」对照排查\n"
        "（能看到=注册正常，是微信过滤；看不到=注册问题）；微信转发列表有缓存，\n"
        "必要时结束微信进程后重开。"
    )
    return lines


def _ensure_assets(assets_dir: Path) -> None:
    """写出两张清单引用的图标（幂等）。"""
    write_png(assets_dir / _ASSET_44, 44)
    write_png(assets_dir / _ASSET_150, 150)


def _note_strategy(strategy: str) -> None:
    """把成功策略落盘，doctor 与排障时提示（策略 B 提醒升级后重注册）。"""
    try:
        layout_dir().joinpath("strategy.txt").write_text(strategy, encoding="utf-8")
    except OSError:
        pass


def _run_powershell(arguments: list[str], timeout: float = 120.0) -> tuple[int, str]:
    """执行一段 PowerShell 命令并返回 (退出码, 合并输出文本)。

    PowerShell 控制台输出编码不定（GBK/UTF-8），先按 utf-8 再按 gbk 解码。
    """
    command = " ".join(arguments)
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, f"无法运行 PowerShell：{error}"
    raw = completed.stdout + completed.stderr
    for encoding in ("utf-8", "gbk"):
        try:
            return completed.returncode, raw.decode(encoding)
        except UnicodeError:
            continue
    return completed.returncode, raw.decode("utf-8", errors="replace")


def _dev_mode_hint(error_text: str) -> str:
    """从注册输出里识别开发者模式问题并给出开启指引。"""
    markers = ("0x80073CFF", "开发人员模式", "developer mode", "Developer Mode")
    if any(marker.lower() in error_text.lower() for marker in markers):
        return (
            "\n提示：注册被拒通常是因为开发者模式未开启。请以管理员身份运行：\n"
            "  reg add HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock"
            " /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f\n"
            "或「设置 → 隐私和安全性 → 开发者选项」打开开发人员模式后重试。"
        )
    return ""
