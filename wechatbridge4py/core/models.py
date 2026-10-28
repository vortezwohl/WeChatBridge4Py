"""SDK 公开数据模型集中地：纯数据与无 IO 的便捷视图。

动词（解析、协议、编排）分布在 transcript / archive / inbox / bridge；
本模块只放名词——类型关系一处可读，也构成 `wechatbridge4py` facade
的全部公开数据类型。ShareBatch 的 transcripts 由 Bridge 在分发前
急切物化（批次目录随回调结束被移动，事后懒解析会拿到失效路径）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Message:
    """一条聊天消息。

    Attributes:
        sender: 发送者昵称（已去除行首「·」与首尾空白）。
        time: 消息时间。来自文本的本地时间，不带时区信息。
        text: 消息正文，保留内部空行与附件引用行（如「[图片] xxx.jpg」）。
    """

    sender: str
    time: datetime
    text: str


@dataclass(frozen=True)
class Transcript:
    """一个归档的解析结果。

    Attributes:
        zip_path: 归档在收件箱内的绝对路径（开发者可自行继续解包媒体）。
        messages: 解析出的消息列表（按归档内顺序）。
        attachment_names: TXT 之外的全部条目名（含 images/、videos/ 前缀）。
    """

    zip_path: Path
    messages: list[Message]
    attachment_names: list[str]


@dataclass(frozen=True)
class SharedItem:
    """manifest.json 里登记的一个批次文件。

    Attributes:
        id: 条目唯一标识。
        display_name: 展示名（微信原始文件名）。
        relative_path: 相对批次目录的 POSIX 风格路径（files/<安全文件名>）。
        path: 消费端加载时填充的绝对路径；批次移动后由 Bridge 重定位，
            回调返回后仍可直接读取文件。
    """

    id: str
    display_name: str
    relative_path: str
    content_type: str | None
    byte_count: int
    item_index: int
    attachment_index: int
    path: str | None = None


@dataclass
class ShareBatch:
    """一次分享到达后交给回调的批次对象（回调拿到的批次已完全物化）。

    Attributes:
        batch_id: 批次唯一标识（Ready 目录名）。
        created_at: 批次提交时间（UTC）。
        action: 来源动作标识（目前恒为 "relay"，保留扩展位）。
        items: 批次内全部文件；``item.path`` 为收件箱内绝对路径。
        transcripts: 各归档解析出的转录（分发前已填充）。
        parse_errors: 各 zip 解析失败原因（不影响 items 本身可用）。
        all_messages: 全部转录的消息按批次内顺序拼接的便捷视图。
        zips: 批次内全部 zip 的绝对路径。
    """

    batch_id: str
    created_at: datetime
    action: str
    items: tuple[SharedItem, ...]
    _batch_dir: Path = field(repr=False, default=None)  # type: ignore[assignment]
    transcripts: list[Transcript] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    @property
    def all_messages(self) -> list[Message]:
        """全部转录的消息（跨归档、按批次内顺序拼接）。"""
        return [message for transcript in self.transcripts for message in transcript.messages]

    @property
    def zips(self) -> list[str]:
        """批次内全部 zip 的绝对路径（需要媒体文件时自行解包）。"""
        return [item.path for item in self.items if (item.path or "").lower().endswith(".zip")]
