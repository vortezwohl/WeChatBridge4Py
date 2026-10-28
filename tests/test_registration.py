"""注册器纯函数部分验证：清单生成、入口 exe 定位、开发者模式自动开启。"""
from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from wechatbridge4py.windows.registration import (
    DISPLAY_NAME,
    PACKAGE_NAME,
    RegistrationError,
    build_manifest,
    share_entry_exe,
)


def test_manifest_is_wellformed_and_carries_contract() -> None:
    xml_text = build_manifest(executable_rel="wcb4py-share.exe", assets_rel="Assets")
    root = ElementTree.fromstring(xml_text)
    namespace = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}
    assert root.find(".//m:Identity", namespace).get("Name") == PACKAGE_NAME
    application = root.find(".//m:Applications/m:Application", namespace)
    assert application.get("Executable") == "wcb4py-share.exe"

    uap = {"uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10"}
    share_target = root.find(".//uap:ShareTarget", uap)
    assert share_target is not None
    assert root.find(".//uap:SupportsAnyFileType", uap) is not None
    formats = [node.text for node in root.findall(".//uap:DataFormat", uap)]
    assert "StorageItems" in formats
    # 契约红线：显示名不得含连续的微信/WeChat/Weixin 子串
    for forbidden in ("微信", "WeChat", "Weixin", "weixin"):
        assert forbidden not in DISPLAY_NAME
    # 布局目录直注形态：不带外部位置声明（未签名注册的实测可行形态）
    assert "AllowExternalContent" not in xml_text


def test_signed_sparse_manifest_carries_external_content_and_publisher() -> None:
    """签名路线清单：外部位置声明 + 发布者与证书主题逐字一致 + 自定义显示名。"""
    xml_text = build_manifest(
        executable_rel="Scripts\\wcb4py-share.exe",
        assets_rel="wcb4py-share\\Assets",
        publisher="CN=WeChatBridge4Py Dev",
        include_external_content=True,
        display_name="我的转发助手",
    )
    root = ElementTree.fromstring(xml_text)
    namespace = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}
    uap10 = {"uap10": "http://schemas.microsoft.com/appx/manifest/uap/windows10/10"}
    assert root.find(".//m:Identity", namespace).get("Publisher") == "CN=WeChatBridge4Py Dev"
    assert root.find(".//uap10:AllowExternalContent", uap10) is not None
    # 开发者的自定义显示名必须写进两处 DisplayName
    assert root.find(".//m:Properties/m:DisplayName", namespace).text == "我的转发助手"
    uap = {"uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10"}
    assert root.find(".//uap:VisualElements", uap).get("DisplayName") == "我的转发助手"


def test_manifest_declares_run_full_trust() -> None:
    """布局目录里的 exe 是完整权限 Win32 进程，必须声明 runFullTrust。"""
    xml_text = build_manifest(executable_rel="wcb4py-share.exe", assets_rel="Assets")
    rescap = {
        "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
    }
    capabilities = [
        node.get("Name")
        for node in ElementTree.fromstring(xml_text).findall(".//rescap:Capability", rescap)
    ]
    assert "runFullTrust" in capabilities


def test_share_entry_exe_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "wcb4py-share.exe"
    fake.write_bytes(b"MZ")
    monkeypatch.setenv("WCB4PY_SHARE_EXE", str(fake))
    assert share_entry_exe() == fake
    monkeypatch.setenv("WCB4PY_SHARE_EXE", str(tmp_path / "missing.exe"))
    with pytest.raises(RegistrationError, match="WCB4PY_SHARE_EXE"):
        share_entry_exe()


def test_ensure_dev_mode_noop_when_already_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """已开启时不做任何动作（不写脚本、不弹提权）。"""
    from wechatbridge4py.windows import registration as reg

    monkeypatch.setattr(reg, "dev_mode_enabled", lambda: True)
    monkeypatch.setattr(
        reg, "_run_powershell", lambda *a, **k: pytest.fail("不应触发提权")
    )
    assert reg.ensure_dev_mode() is True


def test_ensure_dev_mode_elevates_and_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """未开启时写出提权脚本并执行；执行成功（复查通过）即返回 True。"""
    from wechatbridge4py.windows import registration as reg

    state = {"enabled": False}
    monkeypatch.setattr(reg, "dev_mode_enabled", lambda: state["enabled"])
    monkeypatch.setattr(reg, "layout_dir", lambda: tmp_path)

    def fake_run(arguments, timeout=120.0):
        state["enabled"] = True  # 模拟提权脚本写入成功后的复查结果
        return 0, ""

    monkeypatch.setattr(reg, "_run_powershell", fake_run)
    assert reg.ensure_dev_mode() is True
    script = tmp_path / "enable-devmode.ps1"
    assert script.exists()
    assert "AllowDevelopmentWithoutDevLicense" in script.read_text(encoding="ascii")


def test_ensure_dev_mode_respects_no_elevation(monkeypatch: pytest.MonkeyPatch) -> None:
    """allow_elevation=False 时只查询不动作。"""
    from wechatbridge4py.windows import registration as reg

    monkeypatch.setattr(reg, "dev_mode_enabled", lambda: False)
    monkeypatch.setattr(
        reg, "_run_powershell", lambda *a, **k: pytest.fail("不应触发提权")
    )
    assert reg.ensure_dev_mode(allow_elevation=False) is False


def test_ensure_dev_mode_fails_cleanly_when_uac_declined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """UAC 被拒（执行后仍未开启）时安静返回 False，由调用方给手动指引。"""
    from wechatbridge4py.windows import registration as reg

    monkeypatch.setattr(reg, "dev_mode_enabled", lambda: False)
    monkeypatch.setattr(reg, "layout_dir", lambda: tmp_path)
    monkeypatch.setattr(reg, "_run_powershell", lambda a, timeout=120.0: (1, "已取消"))
    assert reg.ensure_dev_mode() is False
