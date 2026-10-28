"""微信合并转发 ZIP 内「聊天记录.txt」的行式转录解析。

微信 4.x 合并转发导出的 TXT 是无文档的行式格式，每条消息由三部分组成::

    ·张三
    2026年9月30日 19:00
    消息正文（可多行，图片/视频以「[图片] 文件名」引用行出现）

本模块用正则按消息头切分正文，天然保留多行与文件内顺序。
"""
from __future__ import annotations

import re
from datetime import datetime

from wechatbridge4py.core.models import Message

# 消息头：行首「·」+ 发送者，换行中文日期时间（时/月/日均可能不补零），再换行进入正文。
HEADER_RE = re.compile(r"(?m)^·([^\r\n]+)\r?\n(\d{4}年\d{1,2}月\d{1,2}日 \d{1,2}:\d{2})\r?\n")

_DATETIME_FORMAT = "%Y年%m月%d日 %H:%M"


class TranscriptError(ValueError):
    """转录文本无法按已知格式解析。"""


def parse_transcript(text: str) -> list[Message]:
    """解析整段聊天记录文本为消息列表。

    Args:
        text: TXT 的完整内容。

    Returns:
        按文件内顺序排列的消息列表。

    Raises:
        TranscriptError: 找不到任何消息头，或首个消息头不在文本开头
            （无法确认开头格式，宁可拒绝也不猜测）。
    """
    matches = list(HEADER_RE.finditer(text))
    if not matches:
        raise TranscriptError("没有找到任何消息头（·发送者 + 时间行）。")
    if matches[0].start() != 0:
        raise TranscriptError("聊天记录开头无法识别：首个消息头不在文本起始处。")

    messages: list[Message] = []
    for index, match in enumerate(matches):
        sender = match.group(1).strip()
        try:
            moment = datetime.strptime(match.group(2), _DATETIME_FORMAT)
        except ValueError as error:
            raise TranscriptError(f"消息时间无法解析：{match.group(2)!r}") from error
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].rstrip("\r\n")
        messages.append(Message(sender=sender, time=moment, text=body))
    return messages
