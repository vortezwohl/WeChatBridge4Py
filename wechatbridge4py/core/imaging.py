"""纯标准库 PNG 编解码与等比缩放：为开发者 logo 生成分享入口图标。

只支持真实世界 logo 的最常见形态：8 位深、非隔行、RGB(A) 的 PNG。
解码用 zlib + 逐行反滤波，编码输出 8 位 RGBA；全程零第三方依赖，
保持 core 子包"仅标准库"的承诺（若环境装有 Pillow，未来可在此层
加快速路径，不改变对外行为）。
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_COLOR_TYPES = {2: 3, 6: 4}  # color_type -> 每像素字节数（RGB=3 / RGBA=4）


class ImageError(ValueError):
    """图像不满足本库支持的形态。消息面向开发者，可直接展示。"""


@dataclass(frozen=True)
class Image:
    """RGBA 位图：每像素 4 字节、行优先、无行间距。"""

    width: int
    height: int
    pixels: bytes


def decode_png(data: bytes) -> Image:
    """解码 PNG 为 RGBA 位图。

    Args:
        data: PNG 文件原始字节。

    Returns:
        RGBA 位图。

    Raises:
        ImageError: 魔数不符、位深非 8、调色板/灰度、隔行扫描、数据损坏。
    """
    if data[:8] != _PNG_SIGNATURE:
        raise ImageError("不是 PNG 文件（魔数不符）。")
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
    position = 8
    while position + 8 <= len(data):
        length = int.from_bytes(data[position:position + 4], "big")
        kind = data[position + 4:position + 8]
        payload = data[position + 8:position + 8 + length]
        position += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            break
    if not width or not height:
        raise ImageError("PNG 缺少有效的 IHDR 数据。")
    if bit_depth != 8:
        raise ImageError(f"仅支持 8 位深 PNG（当前 {bit_depth} 位），请转换后使用。")
    if color_type not in _COLOR_TYPES:
        raise ImageError(
            f"仅支持 RGB / RGBA 的 PNG（当前 color_type={color_type}），"
            "调色板/灰度请先转换。"
        )
    if interlace != 0:
        raise ImageError("不支持隔行（interlaced）PNG，请用非隔行方式导出。")

    source_bpp = _COLOR_TYPES[color_type]
    stride = width * source_bpp
    raw = zlib.decompress(bytes(idat))
    if len(raw) < height * (1 + stride):
        raise ImageError("PNG 像素数据不完整（文件损坏）。")

    rgba = bytearray(width * height * 4)
    cursor = 0
    previous: bytearray | None = None
    for y in range(height):
        filter_type = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        _unfilter_row(filter_type, row, previous, source_bpp)
        previous = row
        base = y * width * 4
        for x in range(width):
            source = x * source_bpp
            target = base + x * 4
            rgba[target] = row[source]
            rgba[target + 1] = row[source + 1]
            rgba[target + 2] = row[source + 2]
            rgba[target + 3] = row[source + 3] if source_bpp == 4 else 255
    return Image(width=width, height=height, pixels=bytes(rgba))


def encode_png(image: Image) -> bytes:
    """把 RGBA 位图编码为 8 位 RGBA、非隔行的 PNG 字节。"""
    stride = image.width * 4
    rows = bytearray()
    for y in range(image.height):
        rows.append(0)  # 过滤器类型 0（None）：编码侧不做过滤，交给 zlib
        rows += image.pixels[y * stride:(y + 1) * stride]

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def scale_contain(image: Image, size: int) -> Image:
    """等比缩放到 size×size 画布内，居中并以透明补边（绝不拉伸变形）。

    Args:
        image: 源位图（建议短边 ≥ 目标尺寸，避免上采样模糊；
            AppIdentity 已保证短边 ≥ 150，故 44/150 两档均为降采样）。
        size: 目标正方形边长。

    Returns:
        size×size 的 RGBA 位图。
    """
    if image.width == size and image.height == size:
        return image
    factor = min(size / image.width, size / image.height)
    scaled_width = max(1, min(size, round(image.width * factor)))
    scaled_height = max(1, min(size, round(image.height * factor)))
    offset_x = (size - scaled_width) // 2
    offset_y = (size - scaled_height) // 2
    canvas = bytearray(size * size * 4)
    for dy in range(scaled_height):
        source_y = dy * image.height // scaled_height
        source_base = source_y * image.width * 4
        target_base = (offset_y + dy) * size * 4 + offset_x * 4
        for dx in range(scaled_width):
            source = source_base + (dx * image.width // scaled_width) * 4
            target = target_base + dx * 4
            canvas[target:target + 4] = image.pixels[source:source + 4]
    return Image(width=size, height=size, pixels=bytes(canvas))


def _unfilter_row(
    filter_type: int,
    row: bytearray,
    previous: bytearray | None,
    bpp: int,
) -> None:
    """按 PNG 规范对一行执行反滤波（0-4 五种过滤器），原地重建。"""
    if filter_type == 0:
        return
    for x in range(len(row)):
        left = row[x - bpp] if x >= bpp else 0
        up = previous[x] if previous is not None else 0
        upper_left = previous[x - bpp] if (previous is not None and x >= bpp) else 0
        if filter_type == 1:  # Sub
            row[x] = (row[x] + left) & 0xFF
        elif filter_type == 2:  # Up
            row[x] = (row[x] + up) & 0xFF
        elif filter_type == 3:  # Average
            row[x] = (row[x] + (left + up) // 2) & 0xFF
        elif filter_type == 4:  # Paeth
            row[x] = (row[x] + _paeth(left, up, upper_left)) & 0xFF
        else:
            raise ImageError(f"未知过滤器类型：{filter_type}")


def _paeth(a: int, b: int, c: int) -> int:
    """PNG 规范的 Paeth 预测子。"""
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c
