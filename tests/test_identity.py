"""AppIdentity 构造期校验的行为验证。"""
from __future__ import annotations

from pathlib import Path

import pytest

from wechatbridge4py.core.imaging import Image, encode_png
from wechatbridge4py.identity import AppIdentity, AppIdentityError


def _logo_png(width: int = 256, height: int = 256) -> bytes:
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            # 左上到右下的双通道渐变
            red = (x * 255) // max(width - 1, 1)
            green = (y * 255) // max(height - 1, 1)
            pixels += bytes((red, green, 128, 255))
    return encode_png(Image(width=width, height=height, pixels=bytes(pixels)))


def test_valid_identity_constructs(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    logo.write_bytes(_logo_png())
    identity = AppIdentity(name="我的笔记助手", logo=str(logo))
    assert identity.logo_bytes() == logo.read_bytes()
    identity_from_bytes = AppIdentity(name="Relay", logo=_logo_png())
    assert len(identity_from_bytes.logo_bytes()) > 0


@pytest.mark.parametrize("bad_name", ["微信助手", "WeChat Relay", "weixin bridge", "  ", "x" * 41])
def test_invalid_names_are_rejected(bad_name: str) -> None:
    with pytest.raises(AppIdentityError):
        AppIdentity(name=bad_name, logo=_logo_png())


def test_missing_logo_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AppIdentityError, match="无法读取"):
        AppIdentity(name="Relay", logo=tmp_path / "missing.png")


def test_non_png_logo_is_rejected(tmp_path: Path) -> None:
    fake = tmp_path / "logo.png"
    fake.write_bytes(b"definitely not a png")
    with pytest.raises(AppIdentityError, match="不是受支持的 PNG"):
        AppIdentity(name="Relay", logo=fake)


def test_undersized_logo_is_rejected() -> None:
    with pytest.raises(AppIdentityError, match="短边"):
        AppIdentity(name="Relay", logo=_logo_png(width=256, height=100))


def test_non_square_logo_is_accepted() -> None:
    """短边达标即可：宽幅 logo 合法（等比缩放 + 补边由注册器处理）。"""
    identity = AppIdentity(name="Relay", logo=_logo_png(width=400, height=200))
    assert identity.name == "Relay"
