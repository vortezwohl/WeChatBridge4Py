"""transcript 解析的行为验证。"""
from __future__ import annotations

from datetime import datetime

import pytest

from tests.conftest import SAMPLE_TXT
from wechatbridge4py.core.transcript import TranscriptError, parse_transcript


def test_parses_senders_times_and_bodies_in_order() -> None:
    messages = parse_transcript(SAMPLE_TXT)
    assert [m.sender for m in messages] == ["张三", "李四", "张三"]
    assert messages[0].time == datetime(2026, 9, 30, 19, 0)  # 非补零月日也能解析
    assert messages[1].time == datetime(2026, 10, 1, 9, 5)
    assert messages[2].time == datetime(2026, 10, 2, 23, 59)


def test_preserves_multiline_body_and_attachment_lines() -> None:
    messages = parse_transcript(SAMPLE_TXT)
    assert messages[1].text == "收到，我更新了文档：\n第二行正文保留\n[图片] 微信图片_20260920_1.jpg"
    assert messages[2].text == "[视频] 视频号演示.mp4"


def test_accepts_crlf_endings() -> None:
    messages = parse_transcript(SAMPLE_TXT.replace("\n", "\r\n"))
    assert len(messages) == 3
    assert messages[0].text == "第一条：今晚发布候选版。"


def test_rejects_text_without_headers() -> None:
    with pytest.raises(TranscriptError):
        parse_transcript("只是普通文本，没有消息头。\n")


def test_rejects_when_first_header_not_at_start() -> None:
    with pytest.raises(TranscriptError):
        parse_transcript("前置噪音\n" + SAMPLE_TXT)
