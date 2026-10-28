"""稀疏包注册：以开发者的应用身份生成清单/图标/资源索引，打包签名并注册。

为什么需要稀疏 MSIX：未打包的普通 exe 无权声明 Share Target。稀疏包
（AllowExternalContent + ExternalLocation）只注册包身份与扩展点，exe 留在
venv 的 Scripts 目录原地，不进 WindowsApps。

显示名与图标来自开发者的 AppIdentity（SDK 侧已做微信过滤红线校验）；
包身份（Name/Publisher）是基础设施常量，与品牌无关——微信过滤发生在
显示名层，包名含 WeChat 已被上游真机验证为不受影响。

分享声明必须宽（SupportsAnyFileType + 多 DataFormat）：窄声明会被微信在
枚举阶段直接剔除，运行时再自行校验只收 zip。

注册路线（按优先级）：
- 签名稀疏包（首选，上游生产同款形态）：自建代码签名证书 → makepri 生成
  resources.pri（上游实测：缺 PRI 会「包能注册但进不了分享面板」）→
  makeappx 打包 → signtool 签名 → Add-AppxPackage -Path -ExternalLocation。
  产物不是开发者模式包（IsDevelopmentMode=False），且 exe 不复制、升级本包后
  无需重注册。
- 开发者模式直注（SDK 工具缺失时的兜底）：布局目录 + Add-AppxPackage
  -Register，产物为未签名开发者模式包；exe 复制进布局目录，升级本包后
  需重跑 register。实测注意：未签名包不能用「清单 + -ExternalLocation」
  直注（0x80073D2E），该组合只有签名 MSIX 可行。
"""
from __future__ import annotations

import glob as glob_module
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from wechatbridge4py.core.imaging import ImageError, decode_png, encode_png, scale_contain
from wechatbridge4py.identity import AppIdentity

# 头部守卫导入（PEP 8 认可的顶部形态）：保证 facade 在非 Windows 平台
# 也可导入，功能调用时再各自降级。
try:
    import winreg
except ImportError:  # 非 Windows 平台
    winreg = None  # type: ignore[assignment]

try:
    from winrt.windows.management.deployment import PackageManager
except ImportError:  # winrt 投影仅随 win32 平台标记安装
    PackageManager = None  # type: ignore[assignment]

PACKAGE_NAME = "WeChatBridge4Py.ShareTarget"
PUBLISHER = "CN=WeChatBridge4Py"
# 签名路线：manifest 的 Publisher 必须与签名证书主题逐字一致，否则身份校验失败。
CERT_SUBJECT = "CN=WeChatBridge4Py Dev"
# 兜底显示名：真实显示名来自开发者的 AppIdentity（红线校验在 identity.py）。
DISPLAY_NAME = "聊天转发桥"
PACKAGE_VERSION = "0.1.0.0"
APP_ID = "Share.Relay"
SHARE_EXE_NAME = "wcb4py-share.exe"

_ASSET_44 = "Square44x44Logo.png"
_ASSET_150 = "Square150x150Logo.png"

_SDK_GLOBS = (
    r"C:\Program Files (x86)\Windows Kits\10\bin\*\x64",
    r"C:\Program Files\Windows Kits\10\bin\*\x64",
    # 新版 SDK 的 makepri 只发布在 x86 目录，必须一并搜索。
    r"C:\Program Files (x86)\Windows Kits\10\bin\*\x86",
    r"C:\Program Files\Windows Kits\10\bin\*\x86",
)


class RegistrationError(RuntimeError):
    """注册/注销失败。消息面向开发者，可直接展示。"""


@dataclass(frozen=True)
class RegisterResult:
    """一次成功注册的结果。

    Attributes:
        strategy: 使用的路线："signed-sparse"（签名稀疏包）或 "layout-copy"（直注兜底）。
        package_full_name: 注册后的包全名（PFN）。
    """

    strategy: str
    package_full_name: str | None


# ---------------------------------------------------------------------------
# 布局与入口定位
# ---------------------------------------------------------------------------

def layout_dir() -> Path:
    """清单、图标、证书与打包工作区所在的目录：<venv>/wcb4py-share。"""
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


# ---------------------------------------------------------------------------
# 清单与图标
# ---------------------------------------------------------------------------

def build_manifest(
    *,
    executable_rel: str,
    assets_rel: str,
    publisher: str = PUBLISHER,
    include_external_content: bool = False,
    display_name: str = DISPLAY_NAME,
    version: str = PACKAGE_VERSION,
) -> str:
    """生成 AppxManifest.xml 文本。

    Args:
        executable_rel: Application/Executable 相对路径（相对外部位置根或布局目录）。
        assets_rel: 图标目录的相对路径前缀。
        publisher: 包发布者；签名路线必须与证书主题逐字一致。
        include_external_content: 签名稀疏路线为 True（配合 -ExternalLocation）。
        display_name: 分享面板展示名（不得含连续 微信/WeChat/Weixin 子串）。
        version: 包版本（四段式）。
    """
    external = (
        "    <uap10:AllowExternalContent>true</uap10:AllowExternalContent>\n"
        if include_external_content
        else ""
    )
    logo44 = f"{assets_rel}\\{_ASSET_44}"
    logo150 = f"{assets_rel}\\{_ASSET_150}"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap uap10 rescap">
  <Identity
    Name="{PACKAGE_NAME}"
    Publisher="{publisher}"
    Version="{version}"
    ProcessorArchitecture="neutral" />
  <Properties>
    <DisplayName>{display_name}</DisplayName>
    <PublisherDisplayName>WeChatBridge4Py</PublisherDisplayName>
    <Logo>{logo150}</Logo>
{external}  </Properties>
  <Resources>
    <Resource Language="zh-cn" />
    <Resource Language="en-us" />
  </Resources>
  <Dependencies>
    <TargetDeviceFamily
      Name="Windows.Desktop"
      MinVersion="10.0.19041.0"
      MaxVersionTested="10.0.26100.0" />
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
    <Application
      Id="{APP_ID}"
      Executable="{executable_rel}"
      uap10:TrustLevel="mediumIL"
      uap10:RuntimeBehavior="win32App">
      <uap:VisualElements
        AppListEntry="none"
        DisplayName="{display_name}"
        Description="把聊天合并转发归档交给你的 Python 程序"
        BackgroundColor="transparent"
        Square44x44Logo="{logo44}"
        Square150x150Logo="{logo150}" />
      <Extensions>
        <uap:Extension Category="windows.shareTarget">
          <!--
            宽声明只为枚举可见性：窄 SupportedFileTypes / 单一 DataFormat
            会被微信在枚举阶段直接剔除。真实负载由入口进程运行时校验（只收 zip）。
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


# ---------------------------------------------------------------------------
# SDK 工具与证书
# ---------------------------------------------------------------------------

def find_sdk_tools() -> dict[str, Path | None]:
    """定位 Windows SDK 的打包/签名工具（取版本最新的一套）。

    Returns:
        {"signtool": 路径或 None, "makeappx": ..., "makepri": ...}。
        环境变量 WCB4PY_SDK_BIN 可直接指定工具所在目录。
    """
    override = os.environ.get("WCB4PY_SDK_BIN")
    if override:
        search_globs = [str(Path(override) / "*.exe")]
    else:
        search_globs = [str(Path(pattern) / "{tool}.exe") for pattern in _SDK_GLOBS]
    found: dict[str, Path | None] = {"signtool": None, "makeappx": None, "makepri": None}
    for tool in found:
        # 按目录顺序（x64 优先、版本从新到旧）取第一个命中即可，
        # 不能全量排序取最后：字符串序里 x86 会反超 x64。
        for pattern in search_globs:
            matches = sorted(
                (Path(p) for p in glob_module.glob(pattern.format(tool=tool))),
                reverse=True,
            )
            if matches:
                found[tool] = matches[0]
                break
    return found


def ensure_certificate() -> str:
    """找到或创建自签名代码签名证书，返回指纹。

    Returns:
        证书指纹（40 位十六进制），供 signtool /sha1 使用。

    Raises:
        RegistrationError: PowerShell 证书操作失败。
    """
    find_cmd = (
        "(Get-ChildItem Cert:\\CurrentUser\\My -CodeSigningCert | "
        "Where-Object { $_.Subject -eq 'CN=WeChatBridge4Py Dev' } | "
        "Sort-Object NotAfter -Descending | Select-Object -First 1).Thumbprint"
    )
    code, output = _run_powershell([find_cmd])
    thumbprint = output.strip()
    if code == 0 and thumbprint and len(thumbprint) == 40:
        return thumbprint

    create_cmd = (
        "$c = New-SelfSignedCertificate -Type Custom -Subject 'CN=WeChatBridge4Py Dev' "
        "-KeyUsage DigitalSignature -FriendlyName 'WeChatBridge4Py Dev' "
        "-CertStoreLocation 'Cert:\\CurrentUser\\My' "
        "-NotAfter (Get-Date).AddYears(5) "
        "-TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3','2.5.29.19={text}'); "
        "$c.Thumbprint"
    )
    code, output = _run_powershell([create_cmd])
    thumbprint = output.strip()
    if code != 0 or not thumbprint or len(thumbprint) != 40:
        raise RegistrationError(f"创建代码签名证书失败：{output.strip()[:400]}")
    return thumbprint


def _export_certificate(thumbprint: str) -> Path:
    """把证书导出为 .cer 文件，返回路径（已存在则先删除）。"""
    cer_path = layout_dir() / "WeChatBridge4Py-Dev.cer"
    cer_path.unlink(missing_ok=True)
    code, output = _run_powershell([
        "Export-Certificate",
        "-Cert", f"'Cert:\\CurrentUser\\My\\{thumbprint}'",
        "-FilePath", f"'{cer_path}'",
    ])
    if code != 0 or not cer_path.exists():
        raise RegistrationError(f"导出证书失败：{output.strip()[:400]}")
    return cer_path


def _ensure_trust(thumbprint: str, cer_path: Path, msix_path: Path) -> None:
    """确保证书进入本机（LocalMachine）受信存储——Appx 部署服务只认本机存储。

    CurrentUser 下的信任对 Add-AppxPackage 无效（实测 0x800B0109），因此
    判定标准必须是本机 TrustedPeople/Root 含该证书。

    Raises:
        RegistrationError: 提权导入被拒或导入后仍不受信（含手动导入指引）。
    """
    if _machine_trusts(thumbprint) and _signature_status(msix_path) == "Valid":
        return

    # 提权导入本机 TrustedPeople/Root（幂等，重复导入无害）。
    script = layout_dir() / "trust-cert.ps1"
    script.write_text(
        "$c = '{cer}'\n"
        "Import-Certificate -FilePath $c "
        "-CertStoreLocation Cert:\\LocalMachine\\TrustedPeople | Out-Null\n"
        "Import-Certificate -FilePath $c "
        "-CertStoreLocation Cert:\\LocalMachine\\Root | Out-Null\n"
        "if ((Get-AuthenticodeSignature -FilePath '{msix}').Status "
        "-eq 'Valid') {{ exit 0 }} else {{ exit 1 }}\n".format(
            cer=str(cer_path), msix=str(msix_path)
        ),
        encoding="ascii",
    )
    code, output = _run_powershell([
        "Start-Process", "-Verb", "RunAs", "-Wait", "-FilePath", "powershell",
        "-ArgumentList", f"'-NoProfile -ExecutionPolicy Bypass -File {script}'",
    ])
    if _machine_trusts(thumbprint) and _signature_status(msix_path) == "Valid":
        return
    raise RegistrationError(
        "签名证书未被本机信任（提权导入被拒或失败："
        f"exit={code} {output.strip()[:300]}）。请手动双击 "
        f"{cer_path} 导入到「本地计算机 → 受信任人」后重试 register。"
    )


def _machine_trusts(thumbprint: str) -> bool:
    """本机 TrustedPeople/Root 存储是否已含该证书（只读查询，无需提权）。"""
    code, output = _run_powershell([
        "[bool](Get-ChildItem Cert:\\LocalMachine\\TrustedPeople, Cert:\\LocalMachine\\Root "
        f"-ErrorAction SilentlyContinue | Where-Object {{ $_.Thumbprint -eq '{thumbprint}' }})"
    ])
    return code == 0 and output.strip().lower() == "true"


def _signature_status(msix_path: Path) -> str:
    """查询 MSIX 的 Authenticode 校验状态（Valid 表示已受信）。"""
    code, output = _run_powershell([
        f"(Get-AuthenticodeSignature -FilePath '{msix_path}').Status.ToString()"
    ])
    return output.strip() if code == 0 else "Unknown"


# ---------------------------------------------------------------------------
# 打包与注册
# ---------------------------------------------------------------------------

def register(
    identity: AppIdentity,
    *,
    allow_dev_mode_elevation: bool = True,
) -> RegisterResult:
    """以开发者的应用身份注册分享入口（幂等：已注册则先注销再注册）。

    Args:
        identity: 应用身份（显示名 + logo），显示名与图标写入清单与资产。
        allow_dev_mode_elevation: 进入兜底直注路线前，若开发者模式未开启，
            是否允许弹 UAC 自动开启（False 时直接给出手动指引并失败）。

    Returns:
        RegisterResult：成功路线与包全名。

    Raises:
        RegistrationError: 两条路线都失败；错误输出包含指引。
    """
    if os.name != "nt":
        raise RegistrationError("register 仅支持 Windows。")
    exe = share_entry_exe()
    target = layout_dir()
    _ensure_assets(target / "Assets", identity)
    if installed_package():
        unregister()

    tools = find_sdk_tools()
    result: RegisterResult | None = None
    if tools["signtool"] and tools["makeappx"]:
        try:
            result = _register_signed(target, tools, identity)
        except RegistrationError as error:
            print(f"[签名路线失败，回退开发者模式直注] {error}")
    if result is None:
        if not ensure_dev_mode(allow_elevation=allow_dev_mode_elevation):
            raise RegistrationError(_DEV_MODE_MANUAL)
        result = _register_layout(exe, target, identity)
    return result


def _register_signed(
    target: Path,
    tools: dict[str, Path | None],
    identity: AppIdentity,
) -> RegisterResult:
    """签名稀疏包路线：证书 → PRI → MSIX → 签名 → 信任 → -ExternalLocation 注册。"""
    print("[1/5] 准备清单与代码签名证书…")
    pack_dir = target / "pack"
    shutil.rmtree(pack_dir, ignore_errors=True)
    (pack_dir / "Assets").mkdir(parents=True)
    (pack_dir / "AppxManifest.xml").write_text(
        build_manifest(
            executable_rel=f"Scripts\\{SHARE_EXE_NAME}",
            assets_rel="wcb4py-share\\Assets",
            publisher=CERT_SUBJECT,
            include_external_content=True,
            display_name=identity.name,
        ),
        encoding="utf-8",
    )
    shutil.copyfile(target / "Assets" / _ASSET_44, pack_dir / "Assets" / _ASSET_44)
    shutil.copyfile(target / "Assets" / _ASSET_150, pack_dir / "Assets" / _ASSET_150)
    thumbprint = ensure_certificate()
    cer_path = _export_certificate(thumbprint)

    print("[2/5] 生成资源索引 resources.pri（微信枚举依赖）…")
    if not _build_resources_pri(pack_dir, tools):
        print("      makepri 不可用或失败，继续（微信可见性可能受影响）")

    print("[3/5] makeappx 打包稀疏 MSIX…")
    msix_path = target / f"{PACKAGE_NAME}.msix"
    msix_path.unlink(missing_ok=True)
    code, output = _run_tool([
        str(tools["makeappx"]), "pack", "/nv", "/h", "SHA256", "/o",
        "/d", str(pack_dir), "/p", str(msix_path),
    ])
    if code != 0 or not msix_path.exists():
        raise RegistrationError(f"makeappx 打包失败：{output.strip()[:400]}")

    print("[4/5] signtool 签名并信任证书（可能弹出 UAC，请允许）…")
    code, output = _run_tool([
        str(tools["signtool"]), "sign", "/fd", "SHA256",
        "/sha1", thumbprint, str(msix_path),
    ])
    if code != 0:
        raise RegistrationError(f"signtool 签名失败：{output.strip()[:400]}")
    _ensure_trust(thumbprint, cer_path, msix_path)

    print("[5/5] 注册签名包（-ExternalLocation 指向虚拟环境）…")
    code, output = _run_powershell([
        "Add-AppxPackage", "-Path", f"'{msix_path}'",
        "-ExternalLocation", f"'{Path(sys.prefix)}'",
    ])
    package = installed_package() if code == 0 else None
    if package is None:
        raise RegistrationError(f"签名包注册失败：exit={code} {output.strip()[:400]}")
    _note_strategy("signed-sparse")
    return RegisterResult(
        strategy="signed-sparse",
        package_full_name=str(package.get("fullName") or ""),
    )


def _register_layout(exe: Path, target: Path, identity: AppIdentity) -> RegisterResult:
    """开发者模式直注兜底路线：复制 exe 到布局目录后 -Register。"""
    shutil.copyfile(exe, target / SHARE_EXE_NAME)
    manifest_path = target / "AppxManifest.xml"
    manifest_path.write_text(
        build_manifest(
            executable_rel=SHARE_EXE_NAME,
            assets_rel="Assets",
            display_name=identity.name,
        ),
        encoding="utf-8",
    )
    code, output = _run_powershell(["Add-AppxPackage", "-Register", str(manifest_path)])
    package = installed_package() if code == 0 else None
    if package is None:
        raise RegistrationError(
            f"注册失败：exit={code} {output.strip()[:600]}" + _dev_mode_hint(output)
        )
    _note_strategy("layout-copy")
    return RegisterResult(
        strategy="layout-copy",
        package_full_name=str(package.get("fullName") or ""),
    )


def _build_resources_pri(pack_dir: Path, tools: dict[str, Path | None]) -> bool:
    """用 makepri 生成 resources.pri；失败不阻断（best effort）。

    注意必须显式 ``/of``：makepri 的输出路径默认跟随当前工作目录，
    不指定时文件会写去别处（实测）。
    """
    makepri = tools.get("makepri")
    if makepri is None:
        return False
    config = pack_dir.parent / "priconfig.xml"
    pri_path = pack_dir / "resources.pri"
    # 两个文件都必须先删：makepri 对已存在的目标会等交互确认，
    # 而它的提示走控制台 API（stdin 重定向无效），无控制台时就是无限挂起（实测）。
    config.unlink(missing_ok=True)
    pri_path.unlink(missing_ok=True)
    code, _ = _run_tool([
        str(makepri), "createconfig", "/cf", str(config), "/dq", "en-US",
    ])
    if code != 0:
        return False
    code, _ = _run_tool([
        str(makepri), "new", "/pr", str(pack_dir), "/cf", str(config),
        "/of", str(pri_path), "/o",
    ])
    return code == 0 and pri_path.exists()


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


# ---------------------------------------------------------------------------
# 查询与体检
# ---------------------------------------------------------------------------

def installed_package() -> dict | None:
    """查询本机已注册的分享包；返回名称/全名/版本/签名形态等，或 None。

    优先走 winrt PackageManager；投影不可用或调用异常时降级 PowerShell。
    """
    if os.name != "nt":
        return None
    if PackageManager is not None:
        try:
            manager = PackageManager()
            for package in manager.find_packages_for_user(""):
                package_id = package.id
                if package_id.name != PACKAGE_NAME:
                    continue
                version = package_id.version
                payload = {
                    "name": package_id.name,
                    "fullName": str(package_id.full_name),
                    "version": (
                        f"{version.major}.{version.minor}."
                        f"{version.build}.{version.revision}"
                    ),
                }
                for key, attribute in (
                    ("signatureKind", "signature_kind"),
                    ("isDevelopmentMode", "is_development_mode"),
                ):
                    try:
                        payload[key] = str(getattr(package, attribute))
                    except Exception:
                        pass
                return payload
            return None  # 枚举成功但没有本包，无需 PowerShell 兜底
        except Exception:
            pass  # winrt 调用异常：落到 PowerShell 兜底
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
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock",
        ) as key:
            return winreg.QueryValueEx(key, "AllowDevelopmentWithoutDevLicense")[0] == 1
    except OSError:
        return False


# 开发者模式的手动开启指引；register 自动开启失败/被拒时兜底展示。
_DEV_MODE_MANUAL = (
    "开发者模式未开启，且自动开启被拒绝或未执行。请以管理员身份运行：\n"
    "  reg add HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock"
    " /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f\n"
    "或「设置 → 系统和安全性 → 开发者选项」打开开发人员模式后重试。"
)


def ensure_dev_mode(allow_elevation: bool = True) -> bool:
    """确保开发者模式开启；未开启时经 UAC 提权自动写入文档化的注册表开关。

    开关即系统设置「开发人员模式」背后的 AppModelUnlock\\
    AllowDevelopmentWithoutDevLicense，立即生效无需重启。HKLM 写入必须
    提权，用户在 UAC 弹窗点一次「是」是下限，无法也不应完全静默。

    Args:
        allow_elevation: 是否允许弹 UAC 自动开启；False 时只查询不动作。

    Returns:
        开发者模式当前是否处于开启状态。
    """
    if dev_mode_enabled():
        return True
    if not allow_elevation:
        return False
    script = layout_dir() / "enable-devmode.ps1"
    script.write_text(
        'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock"'
        " /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f | Out-Null\n"
        '$value = (Get-ItemProperty "HKLM:\\SOFTWARE\\Microsoft\\Windows'
        '\\CurrentVersion\\AppModelUnlock" -ErrorAction SilentlyContinue)'
        ".AllowDevelopmentWithoutDevLicense\n"
        "if ($value -eq 1) { exit 0 } else { exit 1 }\n",
        encoding="ascii",
    )
    print("开发者模式未开启：正在请求管理员权限自动开启（UAC 弹窗请点「是」）…")
    code, output = _run_powershell([
        "Start-Process", "-Verb", "RunAs", "-Wait", "-FilePath", "powershell",
        "-ArgumentList", f"'-NoProfile -ExecutionPolicy Bypass -File {script}'",
    ])
    if dev_mode_enabled():
        print("开发者模式已开启。")
        return True
    print(f"自动开启失败（exit={code} {output.strip()[:200]}）。")
    return False


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _ensure_assets(assets_dir: Path, identity: AppIdentity) -> None:
    """从开发者 logo 生成清单引用的两档图标（44/150，幂等覆盖）。

    Raises:
        RegistrationError: logo 无法读取或解码（构造期已校验过一次，
            这里防御注册时文件被改动的情况）。
    """
    try:
        source = decode_png(identity.logo_bytes())
    except (OSError, ImageError) as error:
        raise RegistrationError(f"应用 logo 无法读取：{error}") from error
    assets_dir.mkdir(parents=True, exist_ok=True)
    for size, name in ((44, _ASSET_44), (150, _ASSET_150)):
        (assets_dir / name).write_bytes(encode_png(scale_contain(source, size)))


def _note_strategy(strategy: str) -> None:
    """把成功路线落盘，doctor 与排障时提示。"""
    try:
        layout_dir().joinpath("strategy.txt").write_text(strategy, encoding="utf-8")
    except OSError:
        pass


def _run_tool(arguments: list[str], timeout: float = 120.0) -> tuple[int, str]:
    """直接调用一个控制台工具（makepri/makeappx/signtool）并返回 (退出码, 输出)。

    三个要点（均为实测教训）：
    - 不经 PowerShell 中转：PS 宿主 + 管道重定向 + 控制台程序会死锁挂起；
    - stdin 必须 DEVNULL：防个别工具等待交互输入；
    - makepri 输出是 UTF-16LE（makeappx/signtool 是 ANSI/UTF-8），
      按「含 \x00 交错」启发式先试 UTF-16。
    """
    try:
        completed = subprocess.run(
            [str(argument) for argument in arguments],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, f"无法运行 {arguments[0]}：{error}"
    raw = completed.stdout + completed.stderr
    if raw[:40].count(b"\x00") >= 8:  # UTF-16 文本的典型字节交错特征
        return completed.returncode, raw.decode("utf-16-le", errors="replace")
    for encoding in ("utf-8", "gbk"):
        try:
            return completed.returncode, raw.decode(encoding)
        except UnicodeError:
            continue
    return completed.returncode, raw.decode("utf-8", errors="replace")


def _run_powershell(arguments: list[str], timeout: float = 120.0) -> tuple[int, str]:
    """执行一段 PowerShell 命令并返回 (退出码, 合并输出文本)。

    只用于 cmdlet（Add-AppxPackage / 证书操作等）；SDK 控制台工具请走
    :func:`_run_tool`。PowerShell 输出编码不定（GBK/UTF-8），按序尝试解码。
    """
    command = " ".join(str(argument) for argument in arguments)
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            stdin=subprocess.DEVNULL,
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
    """从注册输出里识别开发者模式问题，追加手动开启指引。"""
    markers = ("0x80073CFF", "开发人员模式", "developer mode", "Developer Mode")
    if any(marker.lower() in error_text.lower() for marker in markers):
        return "\n提示：" + _DEV_MODE_MANUAL
    return ""
