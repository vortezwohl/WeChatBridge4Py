"""imaging 纯标准库 PNG 编解码与缩放的行为验证。"""
from __future__ import annotations

import struct
import zlib

import pytest

from wechatbridge4py.core.imaging import (
    Image,
    ImageError,
    decode_png,
    encode_png,
    scale_contain,
)


def _solid(width: int, height: int, rgba: tuple[int, int, int, int]) -> Image:
    pixels = bytes(rgba) * (width * height)
    return Image(width=width, height=height, pixels=pixels)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _patch_ihdr(png: bytes, **fields: int) -> bytes:
    """重写首个 IHDR 的字段并重算 CRC（构造非法 PNG 用）。

    PNG 布局：8 字节签名 + IHDR 块（4 长度 + 4 类型 + 13 数据 + 4 CRC），
    即 IHDR 数据位于 png[16:29]，整块止于 png[33]。
    """
    values = list(struct.unpack(">IIBBBBB", png[16:29]))
    names = ["width", "height", "depth", "color_type", "compression", "filter", "interlace"]
    for name, value in fields.items():
        values[names.index(name)] = value
    return png[:8] + _chunk(b"IHDR", struct.pack(">IIBBBBB", *values)) + png[33:]


def test_encode_decode_roundtrip() -> None:
    source = _solid(3, 2, (10, 200, 30, 255))
    decoded = decode_png(encode_png(source))
    assert (decoded.width, decoded.height) == (3, 2)
    assert decoded.pixels == source.pixels


def test_decode_rejects_non_png_magic() -> None:
    with pytest.raises(ImageError, match="魔数"):
        decode_png(b"BM\x00\x00not a png")


def test_decode_rejects_palette_png() -> None:
    """color_type=3（调色板）在受支持形态之外。"""
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
        + _chunk(b"PLTE", b"\x01\x02\x03")
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + _chunk(b"IEND", b"")
    )
    with pytest.raises(ImageError, match="RGB / RGBA"):
        decode_png(png)


def test_decode_rejects_16bit_depth() -> None:
    source = encode_png(_solid(1, 1, (1, 2, 3, 255)))
    patched = _patch_ihdr(source, depth=16)
    with pytest.raises(ImageError, match="8 位深"):
        decode_png(patched)


def test_scale_contain_preserves_aspect_and_pads_transparent() -> None:
    """非正方形 logo：等比缩放、居中、四周透明补边，不拉伸。"""
    source = _solid(200, 100, (200, 30, 30, 255))
    scaled = scale_contain(source, 150)
    assert (scaled.width, scaled.height) == (150, 150)
    pixels = scaled.pixels
    # 中心行应为实体色；顶边应为透明补边
    center = 75 * 150 * 4 + 75 * 4
    assert tuple(pixels[center:center + 4]) == (200, 30, 30, 255)
    top = 0 * 150 * 4 + 75 * 4
    assert tuple(pixels[top:top + 4]) == (0, 0, 0, 0)


def test_scale_contain_identity_when_square_match() -> None:
    source = _solid(150, 150, (9, 9, 9, 255))
    assert scale_contain(source, 150) is source
