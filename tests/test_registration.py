"""注册器纯函数部分验证：清单生成、PNG 编码、入口 exe 定位。"""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from wechatbridge4py.win.registration import (
    DISPLAY_NAME,
    PACKAGE_NAME,
    RegistrationError,
    build_manifest,
    share_entry_exe,
    write_png,
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


def test_manifest_declares_run_full_trust() -> None:
    """布局目录里的 exe 是完整权限 Win32 进程，必须声明 runFullTrust。"""
    xml_text = build_manifest(executable_rel="wcb4py-share.exe", assets_rel="Assets")
    rescap = {
        "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
    }
    capabilities = [
        node.get("Name") for node in ElementTree.fromstring(xml_text).findall(".//rescap:Capability", rescap)
    ]
    assert "runFullTrust" in capabilities


def test_signed_sparse_manifest_carries_external_content_and_publisher() -> None:
    """签名路线清单：外部位置声明 + 发布者与证书主题逐字一致。"""
    xml_text = build_manifest(
        executable_rel="Scripts\\wcb4py-share.exe",
        assets_rel="wcb4py-share\\Assets",
        publisher="CN=WeChatBridge4Py Dev",
        include_external_content=True,
    )
    root = ElementTree.fromstring(xml_text)
    namespace = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}
    uap10 = {"uap10": "http://schemas.microsoft.com/appx/manifest/uap/windows10/10"}
    assert root.find(".//m:Identity", namespace).get("Publisher") == "CN=WeChatBridge4Py Dev"
    assert root.find(".//uap10:AllowExternalContent", uap10) is not None
    # 兜底路线（默认）则不带外部位置声明
    fallback = build_manifest(executable_rel="wcb4py-share.exe", assets_rel="Assets")
    assert "AllowExternalContent" not in fallback


def test_write_png_emits_valid_png(tmp_path: Path) -> None:
    target = tmp_path / "icon.png"
    write_png(target, 44)
    raw = target.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", raw[16:24])
    assert (width, height) == (44, 44)
    assert raw.endswith(b"IEND\xaeB`\x82")


def test_share_entry_exe_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "wcb4py-share.exe"
    fake.write_bytes(b"MZ")
    monkeypatch.setenv("WCB4PY_SHARE_EXE", str(fake))
    assert share_entry_exe() == fake
    monkeypatch.setenv("WCB4PY_SHARE_EXE", str(tmp_path / "missing.exe"))
    with pytest.raises(RegistrationError, match="WCB4PY_SHARE_EXE"):
        share_entry_exe()
