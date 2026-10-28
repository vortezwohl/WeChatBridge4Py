"""微信合并转发 ZIP 的安全读取：只读、不解压落盘、防御式校验。

微信导出的归档结构（上游项目与真机确认）::

    聊天记录.txt          ← 行式转录（transcript 模块负责解析）
    images/<原名>.jpg     ← 图片
    videos/<原名>.mp4     ← 视频

本模块站在不可信输入的边界上：条目数、总解压尺寸、加密位、压缩方法、
路径穿越与重名都做硬校验，任何一项不满足立即整档拒绝（fail-closed）。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from wechatbridge4py.core.models import Message, Transcript
from wechatbridge4py.core.transcript import TranscriptError, parse_transcript

# 与上游一致的防御上限。
MAX_ENTRIES = 1000
MAX_TOTAL_UNCOMPRESSED = 1 << 30  # 1 GiB
MAX_TRANSCRIPT_BYTES = 16 << 20   # 单个 TXT 最多读 16 MiB

_TRANSCRIPT_NAME = "聊天记录.txt"


class ArchiveError(ValueError):
    """归档不合规或无法解析。消息面向开发者，可直接展示。"""


def read_archive(zip_path: str | Path) -> Transcript:
    """读取并解析一个微信合并转发归档。

    Args:
        zip_path: 归档文件路径（通常来自批次条目）。

    Returns:
        解析出的转录（消息 + 附件条目名）。

    Raises:
        ArchiveError: 归档损坏、加密、超限、路径不安全，或没有任何
            可解析的文本转录。
    """
    path = Path(zip_path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            _guard_entries(infos)
            text_infos = [info for info in infos if info.filename.lower().endswith(".txt")]
            if not text_infos:
                raise ArchiveError(f"归档中没有文本转录文件：{path.name}")

            attachments = [info.filename for info in infos if info not in text_infos]

            # 首选微信的固定名；否则扫描全部 txt，取解析出消息最多的那个。
            preferred = [info for info in text_infos if Path(info.filename).name == _TRANSCRIPT_NAME]
            ordered = preferred + [info for info in text_infos if info not in preferred]
            best_messages: list[Message] | None = None
            last_error: Exception | None = None
            for info in ordered:
                if info.file_size > MAX_TRANSCRIPT_BYTES:
                    last_error = ArchiveError(f"文本转录过大：{info.filename}")
                    continue
                try:
                    raw = archive.read(info)
                    best_messages = parse_transcript(_decode_text(raw))
                    break
                except (TranscriptError, UnicodeError, zipfile.BadZipFile) as error:
                    last_error = error
                    continue
            if best_messages is None:
                raise ArchiveError(
                    f"无法从归档解析聊天文本：{last_error or '没有可读的 txt 条目'}"
                )
            return Transcript(zip_path=path, messages=best_messages, attachment_names=attachments)
    except zipfile.BadZipFile as error:
        raise ArchiveError(f"不是有效的 zip 归档：{path.name}（{error}）") from error


def _guard_entries(infos: list[zipfile.ZipInfo]) -> None:
    """整档防御校验：条目数、加密、压缩法、总量、路径安全与重名。"""
    if not infos:
        raise ArchiveError("归档为空。")
    if len(infos) > MAX_ENTRIES:
        raise ArchiveError(f"归档条目过多（{len(infos)} > {MAX_ENTRIES}）。")
    total = 0
    seen: set[str] = set()
    for info in infos:
        if info.flag_bits & 0x1:
            raise ArchiveError(f"归档条目已加密，拒绝处理：{info.filename}")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ArchiveError(f"不支持的压缩方法（{info.compress_type}）：{info.filename}")
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise ArchiveError("归档解压总量超过 1 GiB，拒绝处理。")
        normalized = info.filename.replace("\\", "/")
        parts = normalized.split("/")
        if normalized.startswith("/") or ":" in normalized or ".." in parts:
            raise ArchiveError(f"归档条目路径不安全：{info.filename}")
        folded = "/".join(parts).casefold()
        if folded in seen:
            raise ArchiveError(f"归档内条目重名：{info.filename}")
        seen.add(folded)


def _decode_text(raw: bytes) -> str:
    """按 utf-8 → gbk 的顺序尝试解码转录文本。"""
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        return raw.decode("gbk")
