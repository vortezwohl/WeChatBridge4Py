"""测试夹具：临时收件箱与微信导出归档的构造器。"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from wechatbridge4py.core.inbox import InboxPaths

SAMPLE_TXT = (
    "·张三\n"
    "2026年9月30日 19:00\n"
    "第一条：今晚发布候选版。\n"
    "\n"
    "·李四\n"
    "2026年10月1日 9:05\n"
    "收到，我更新了文档：\n"
    "第二行正文保留\n"
    "[图片] 微信图片_20260920_1.jpg\n"
    "\n"
    "·张三\n"
    "2026年10月2日 23:59\n"
    "[视频] 视频号演示.mp4\n"
)


@pytest.fixture()
def inbox(tmp_path: Path) -> InboxPaths:
    """独立的临时收件箱（目录由 InboxPaths 惰性创建）。"""
    return InboxPaths(tmp_path / "Inbox")


def make_wechat_zip(target: Path, txt: str = SAMPLE_TXT, with_media: bool = True) -> Path:
    """构造一个与微信合并转发同构的 zip：聊天记录.txt + images/ + videos/。"""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("聊天记录.txt", txt)
        if with_media:
            archive.writestr("images/微信图片_20260920_1.jpg", b"\xff\xd8fake-jpeg")
            archive.writestr("videos/视频号演示.mp4", b"\x00\x00\x00\x18fake-mp4")
    return target


@pytest.fixture()
def wechat_zip(tmp_path: Path) -> Path:
    """标准三消息归档。"""
    return make_wechat_zip(tmp_path / "聊天记录_20261002.zip")
