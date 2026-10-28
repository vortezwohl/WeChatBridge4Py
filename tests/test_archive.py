"""archive 安全读取的行为验证。"""
from __future__ import annotations

import zipfile

import pytest

from tests.conftest import make_wechat_zip
from wechatbridge4py.core.archive import ArchiveError, read_archive


def test_reads_messages_and_lists_attachments(tmp_path) -> None:
    path = make_wechat_zip(tmp_path / "a.zip")
    transcript = read_archive(path)
    assert len(transcript.messages) == 3
    assert "images/微信图片_20260920_1.jpg" in transcript.attachment_names
    assert "videos/视频号演示.mp4" in transcript.attachment_names
    assert "聊天记录.txt" not in transcript.attachment_names


def test_prefers_canonical_transcript_name(tmp_path) -> None:
    """存在多个 txt 时优先解析微信固定名「聊天记录.txt」。"""
    with zipfile.ZipFile(tmp_path / "b.zip", "w") as archive:
        archive.writestr("说明.txt", "不是聊天记录")
        archive.writestr("聊天记录.txt", "·甲\n2026年1月1日 08:00\n内容\n")
    transcript = read_archive(tmp_path / "b.zip")
    assert [m.sender for m in transcript.messages] == ["甲"]


def test_rejects_zip_without_txt(tmp_path) -> None:
    with zipfile.ZipFile(tmp_path / "c.zip", "w") as archive:
        archive.writestr("images/x.jpg", b"junk")
    with pytest.raises(ArchiveError, match="文本转录"):
        read_archive(tmp_path / "c.zip")


def test_rejects_path_traversal_entry(tmp_path) -> None:
    with zipfile.ZipFile(tmp_path / "d.zip", "w") as archive:
        archive.writestr("聊天记录.txt", "·甲\n2026年1月1日 08:00\n内容\n")
        archive.writestr("../evil.txt", b"boom")
    with pytest.raises(ArchiveError, match="路径不安全"):
        read_archive(tmp_path / "d.zip")


def test_rejects_not_a_zip(tmp_path) -> None:
    target = tmp_path / "e.zip"
    target.write_bytes(b"this is not a zip")
    with pytest.raises(ArchiveError, match="不是有效的 zip"):
        read_archive(target)


def test_rejects_duplicate_entry_names_case_insensitive(tmp_path) -> None:
    with zipfile.ZipFile(tmp_path / "f.zip", "w") as archive:
        archive.writestr("聊天记录.txt", "·甲\n2026年1月1日 08:00\n内容\n")
        archive.writestr("Images/a.jpg", b"x")
        archive.writestr("images/A.JPG", b"x")
    with pytest.raises(ArchiveError, match="重名"):
        read_archive(tmp_path / "f.zip")
