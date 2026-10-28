"""inbox 文件协议的行为验证：暂存、原子发布、准入校验、失败落档。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.conftest import make_wechat_zip
from wechatbridge4py.core.inbox import InboxError, InboxPaths, InboxWriter, SourceFile, parse_iso_z


def _source(zip_path: Path) -> SourceFile:
    return SourceFile(path=str(zip_path), display_name=zip_path.name)


def test_stage_then_commit_publishes_atomically(inbox: InboxPaths, wechat_zip: Path) -> None:
    writer = InboxWriter(inbox)
    staged = writer.stage([_source(wechat_zip)])
    # 暂存完成但未发布：Ready 必须仍然为空（消费者可见即完整）
    assert not any(inbox.ready.iterdir())
    ready = writer.commit(staged)
    manifest = json.loads((ready / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 1
    assert manifest["batchId"] == staged.batch_id
    assert manifest["createdAt"].endswith("Z")
    parse_iso_z(manifest["createdAt"])
    item = manifest["items"][0]
    assert item["displayName"] == wechat_zip.name
    assert item["byteCount"] == wechat_zip.stat().st_size
    assert (ready / item["relativePath"]).read_bytes() == wechat_zip.read_bytes()


def test_rejects_non_zip_and_records_failure(inbox: InboxPaths, tmp_path: Path) -> None:
    bad = tmp_path / "note.txt"
    bad.write_text("hello", encoding="utf-8")
    writer = InboxWriter(inbox)
    with pytest.raises(InboxError, match="只接收"):
        writer.stage([_source(bad)])
    assert not any(inbox.staging.iterdir())  # 残骸已清
    failures = list(inbox.failed.glob("*.json"))
    assert len(failures) == 1
    payload = json.loads(failures[0].read_text(encoding="utf-8"))
    assert payload["message"].startswith("只接收")


def test_rejects_missing_file(inbox: InboxPaths, tmp_path: Path) -> None:
    writer = InboxWriter(inbox)
    with pytest.raises(InboxError, match="找不到"):
        writer.stage([SourceFile(path=str(tmp_path / "ghost.zip"), display_name="ghost.zip")])


def test_duplicate_names_get_suffixes(inbox: InboxPaths, tmp_path: Path) -> None:
    first = make_wechat_zip(tmp_path / "聊天记录.zip")
    second = make_wechat_zip(tmp_path / "聊天记录_2.zip", txt="·乙\n2026年1月2日 08:00\n二\n")
    writer = InboxWriter(inbox)
    staged = writer.stage([
        SourceFile(path=str(first), display_name="聊天记录.zip", item_index=0),
        SourceFile(path=str(second), display_name="聊天记录.zip", item_index=1),
    ])
    ready = writer.commit(staged)
    names = sorted(p.name for p in (ready / "files").iterdir())
    assert names == ["聊天记录 (2).zip", "聊天记录.zip"]


def test_rejects_empty_share(inbox: InboxPaths) -> None:
    with pytest.raises(InboxError, match="没有可处理"):
        InboxWriter(inbox).stage([])


def test_prune_staging_removes_only_stale_trees(inbox: InboxPaths, wechat_zip: Path) -> None:
    writer = InboxWriter(inbox)
    fresh = writer.stage([_source(wechat_zip)])
    stale = writer.stage([_source(wechat_zip)])
    old = time.time() - 3600
    for child in stale.staging_dir.rglob("*"):
        import os

        os.utime(child, (old, old))
    import os

    os.utime(stale.staging_dir, (old, old))
    removed = inbox.prune_staging()
    assert removed == 1
    assert fresh.staging_dir.exists()
    assert not stale.staging_dir.exists()
