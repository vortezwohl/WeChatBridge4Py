"""Bridge 消费行为验证：分发、Done/Failed 归档、幂等与懒解析。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.conftest import make_wechat_zip
from wechatbridge4py.bridge import Bridge
from wechatbridge4py.core.inbox import InboxPaths, InboxWriter, SourceFile


def _inject(inbox: InboxPaths, zip_path) -> str:
    writer = InboxWriter(inbox)
    staged = writer.stage([SourceFile(path=str(zip_path), display_name=zip_path.name)])
    writer.commit(staged)
    return staged.batch_id


def test_drain_delivers_batch_and_moves_to_done(inbox: InboxPaths, wechat_zip) -> None:
    batch_id = _inject(inbox, wechat_zip)
    seen: list = []
    bridge = Bridge(inbox=inbox)
    bridge.on_share(lambda batch: seen.append(batch))
    processed = bridge.drain()

    assert len(processed) == 1 and seen[0].batch_id == batch_id
    assert len(seen[0].all_messages) == 3
    assert seen[0].items[0].path.endswith(".zip")
    assert (inbox.done / batch_id).is_dir()
    assert not any(inbox.ready.iterdir())
    assert bridge.drain() == []  # 幂等：不会重投
    # 回调返回后批次目录已移入 Done，但路径已重定位，文件仍可直接读取
    assert Path(seen[0].zips[0]).exists()
    assert f"Done{os.sep}" in seen[0].zips[0]


def test_failing_handler_moves_batch_to_failed_with_report(
    inbox: InboxPaths, wechat_zip
) -> None:
    batch_id = _inject(inbox, wechat_zip)
    bridge = Bridge(inbox=inbox)

    @bridge.on_share
    def broken(batch) -> None:
        raise RuntimeError("下游崩了")

    processed = bridge.drain()
    assert len(processed) == 1
    quarantined = inbox.failed / batch_id
    assert (quarantined / "error.json").is_dir() is False
    report = json.loads((quarantined / "error.json").read_text(encoding="utf-8"))
    assert "下游崩了" in report["message"]
    assert not any(inbox.ready.iterdir())  # 失败批次不阻塞后续


def test_on_share_works_as_decorator(inbox: InboxPaths, wechat_zip) -> None:
    _inject(inbox, wechat_zip)
    bridge = Bridge(inbox=inbox)
    calls: list[str] = []

    @bridge.on_share
    def handler(batch) -> None:
        calls.append(batch.batch_id)

    bridge.drain()
    assert len(calls) == 1


def test_transcripts_lazily_parsed_and_errors_captured(
    inbox: InboxPaths, tmp_path
) -> None:
    good = make_wechat_zip(tmp_path / "good.zip")
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip at all")
    writer = InboxWriter(inbox)
    staged = writer.stage([
        SourceFile(path=str(good), display_name="good.zip", item_index=0),
        SourceFile(path=str(bad), display_name="bad.zip", item_index=1),
    ])
    writer.commit(staged)

    bridge = Bridge(inbox=inbox)
    got: list = []
    bridge.on_share(got.append)
    bridge.drain()

    batch = got[0]
    assert len(batch.zips) == 2
    assert len(batch.transcripts) == 1  # 只有好档解析成功
    assert len(batch.parse_errors) == 1
    assert "bad.zip" in batch.parse_errors[0]


def test_broken_manifest_is_quarantined_not_stuck(inbox: InboxPaths) -> None:
    inbox.ensure_created()
    junk = inbox.ready / "00000000-0000-0000-0000-000000000000"
    (junk / "files").mkdir(parents=True)
    (junk / "manifest.json").write_text("{ not json", encoding="utf-8")

    bridge = Bridge(inbox=inbox)
    assert bridge.drain() == []
    assert (junk / "error.json").exists() is False  # 已整体移走
    assert any(inbox.failed.iterdir())
    assert not any(inbox.ready.iterdir())


def test_register_requires_app_identity(inbox: InboxPaths) -> None:
    """纯消费模式（未提供 app）调用 register 必须立刻拒绝。"""
    bridge = Bridge(inbox=inbox)
    with pytest.raises(ValueError, match="应用身份"):
        bridge.register()


def test_inject_simulates_share_without_wechat(inbox: InboxPaths, wechat_zip) -> None:
    """inject = 一次模拟分享：返回批次 id，drain 恰好分发一次。"""
    bridge = Bridge(inbox=inbox)
    got: list = []
    bridge.on_share(got.append)

    batch_id = bridge.inject(str(wechat_zip))
    assert isinstance(batch_id, str) and len(batch_id) == 36  # UUID 形态
    assert any(inbox.ready.iterdir())  # 尚未消费：批次还在 Ready 里

    processed = bridge.drain()
    assert [batch.batch_id for batch in processed] == [batch_id]
    assert got and got[0].batch_id == batch_id
    assert len(got[0].all_messages) == 3
    assert not any(inbox.ready.iterdir())  # 消费后进入 Done
    assert (inbox.done / batch_id).is_dir()


def test_inject_missing_file_raises(inbox: InboxPaths, tmp_path) -> None:
    bridge = Bridge(inbox=inbox)
    with pytest.raises(FileNotFoundError):
        bridge.inject(str(tmp_path / "ghost.zip"))
