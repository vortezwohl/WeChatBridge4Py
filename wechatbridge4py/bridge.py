"""开发者侧消费端：监听收件箱，把每个新批次交给回调。

Bridge 是本库面向开发者的核心对象。它不关心批次从哪来（微信分享入口、
demo-share 注入、或手工放进 Ready 的目录），只保证：

- 启动即补扫（漏掉的事件信号不会丢批次）；
- 每个批次恰好处理一次：回调全部成功 → 移入 Done；任一回调抛异常 →
  连同错误报告移入 Failed，绝不阻塞后续批次；
- 至少一次语义：进程若在回调后、移动前崩溃，下次启动会重投该批次，
  回调需要容忍偶发重复。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from wechatbridge4py.core.archive import ArchiveError, Transcript, read_archive
from wechatbridge4py.core.inbox import (
    MANIFEST_NAME,
    SCHEMA_VERSION,
    InboxPaths,
    SharedItem,
    now_iso_z,
    parse_iso_z,
)


@dataclass
class ShareBatch:
    """一次分享到达后交给回调的批次对象。

    回调拿到的批次已经完全物化：transcripts 在分发前解析完毕（批次目录
    随后会从 Ready 移入 Done/Failed，基于旧路径的懒解析会失效），移动后
    ``items[].path`` 会同步重定位到新目录，回调返回后仍可直接读取文件。

    Attributes:
        batch_id: 批次唯一标识（Ready 目录名）。
        created_at: 批次提交时间（UTC）。
        action: 来源动作标识（目前恒为 "relay"，保留扩展位）。
        items: 批次内全部文件；``item.path`` 为收件箱内绝对路径。
        parse_errors: 各 zip 解析失败原因（不影响 items 本身可用）。
        transcripts: 各归档解析出的转录。
        all_messages: 全部转录的消息按序拼接的便捷视图。
    """

    batch_id: str
    created_at: datetime
    action: str
    items: tuple[SharedItem, ...]
    _batch_dir: Path = field(repr=False, default=None)  # type: ignore[assignment]
    parse_errors: list[str] = field(default_factory=list)
    _transcripts: list[Transcript] | None = field(default=None, repr=False)

    @property
    def transcripts(self) -> list[Transcript]:
        """解析结果（由 Bridge 在分发前填充；直接构造时按需解析）。"""
        if self._transcripts is None:
            self._transcripts, self.parse_errors = _parse_items(self.items)
        return self._transcripts

    @property
    def all_messages(self):
        """全部转录的消息（跨归档、按批次内顺序拼接）。"""
        return [message for transcript in self.transcripts for message in transcript.messages]

    @property
    def zips(self) -> list[str]:
        """批次内全部 zip 的绝对路径（需要媒体文件时自行解包）。"""
        return [item.path for item in self.items if (item.path or "").lower().endswith(".zip")]


ShareHandler = Callable[[ShareBatch], object]


class Bridge:
    """收件箱消费器：注册回调、补扫、等待信号、逐批分发。

    Args:
        inbox: 收件箱根目录或 :class:`InboxPaths`；缺省用环境变量/系统默认。
        poll_interval: 无信号平台的轮询周期（秒），Windows 上仅作兜底。

    用法::

        bridge = Bridge()

        @bridge.on_share
        def handle(batch: ShareBatch) -> None:
            ...

        bridge.run_forever()
    """

    def __init__(self, inbox: str | os.PathLike[str] | InboxPaths | None = None,
                 poll_interval: float = 0.5) -> None:
        self.paths = inbox if isinstance(inbox, InboxPaths) else InboxPaths(inbox)
        self.poll_interval = poll_interval
        self._handlers: list[ShareHandler] = []

    def on_share(self, handler: ShareHandler) -> ShareHandler:
        """注册一个分享回调；可直接作装饰器使用。可注册多个，按序调用。"""
        self._handlers.append(handler)
        return handler

    def drain(self) -> list[ShareBatch]:
        """处理 Ready 内当前全部批次（启动补扫与信号触发共用入口）。

        Returns:
            本次处理过的批次列表（含成功与失败，失败者已移入 Failed）。
        """
        self.paths.ensure_created()
        batches = [(entry, self._load(entry)) for entry in self._pending_dirs()]
        processed: list[ShareBatch] = []
        for entry, batch in batches:
            if batch is None:
                _quarantine(entry, self.paths, "manifest 缺失或不可解析")
                continue
            try:
                for handler in self._handlers:
                    handler(batch)
            except Exception as error:
                self.paths.log(f"批次回调失败 batch={batch.batch_id}", error)
                new_dir = _quarantine(entry, self.paths, f"回调异常：{error.__class__.__name__}: {error}")
            else:
                new_dir = _move_dir(entry, self.paths.done)
            if new_dir is not None:
                _relocate(batch, new_dir)
            self.paths.log(f"[trace] batch consumed id={batch.batch_id}")
            processed.append(batch)
        return processed

    def run_forever(self) -> None:
        """阻塞消费循环：先补扫一次，然后等待命名事件/轮询周期触发。

        Ctrl+C 退出。Windows 下等待命名事件（零 CPU），其他平台退化为轮询。
        """
        self.paths.ensure_created()
        self.paths.prune_staging()
        self.drain()
        waiter = self._open_waiter()
        try:
            while True:
                if waiter is not None:
                    waiter.wait(max(int(self.poll_interval * 1000), 100))
                else:
                    time.sleep(self.poll_interval)
                self.drain()
        except KeyboardInterrupt:
            pass
        finally:
            if waiter is not None:
                waiter.close()

    def _pending_dirs(self) -> list[Path]:
        """Ready 内待处理批次目录，按创建时间排序保证投递顺序。"""
        entries = [entry for entry in self.paths.ready.iterdir() if entry.is_dir()]
        return sorted(entries, key=lambda p: (p.stat().st_ctime, p.name))

    def _load(self, entry: Path) -> ShareBatch | None:
        """读取单个批次的 manifest 并装配 ShareBatch；结构不符返回 None。"""
        try:
            manifest = json.loads((entry / MANIFEST_NAME).read_text(encoding="utf-8"))
            if int(manifest.get("schemaVersion", 0)) != SCHEMA_VERSION:
                return None
            items = tuple(
                SharedItem(
                    id=item["id"],
                    display_name=item["displayName"],
                    relative_path=item["relativePath"],
                    content_type=item.get("contentType"),
                    byte_count=int(item.get("byteCount", 0)),
                    item_index=int(item.get("itemIndex", 0)),
                    attachment_index=int(item.get("attachmentIndex", 0)),
                    path=str(entry / item["relativePath"]),
                )
                for item in manifest.get("items", [])
            )
            # 分发前即物化转录：批次目录随回调结束被移动，事后懒解析会拿到失效路径。
            transcripts, parse_errors = _parse_items(items)
            return ShareBatch(
                batch_id=str(manifest["batchId"]),
                created_at=parse_iso_z(manifest["createdAt"]),
                action=str(manifest.get("action", "relay")),
                items=items,
                _batch_dir=entry,
                parse_errors=parse_errors,
                _transcripts=transcripts,
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def _open_waiter():
        """Windows 上打开命名事件等待器；其他平台/失败时返回 None 走轮询。"""
        if sys.platform == "win32":
            try:
                from wechatbridge4py.win.events import InboxEventWaiter

                return InboxEventWaiter()
            except Exception:
                return None
        return None


def _move_dir(source: Path, target_dir: Path) -> Path | None:
    """把批次目录移入 Done/Failed；返回新目录（移动彻底失败时返回 None）。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / source.name
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    try:
        os.rename(source, destination)
        return destination
    except OSError:
        shutil.rmtree(source, ignore_errors=True)
        return destination if destination.exists() else None


def _relocate(batch: ShareBatch, new_dir: Path) -> None:
    """批次目录移动后，把 items 的绝对路径重定位到新目录（回调返回后仍可读文件）。"""
    batch._batch_dir = new_dir
    batch.items = tuple(replace(item, path=str(new_dir / item.relative_path)) for item in batch.items)


def _parse_items(items: tuple[SharedItem, ...]) -> tuple[list[Transcript], list[str]]:
    """解析批次内全部 zip；单档失败记为错误说明，绝不让个别坏档阻塞整批。"""
    results: list[Transcript] = []
    errors: list[str] = []
    for item in items:
        if not (item.path or "").lower().endswith(".zip"):
            continue
        try:
            results.append(read_archive(item.path))
        except (ArchiveError, OSError) as error:
            errors.append(f"{item.display_name}: {error}")
    return results, errors


def _quarantine(entry: Path, paths: InboxPaths, reason: str) -> Path | None:
    """把无法消费的批次连同错误报告移入 Failed，返回新目录。"""
    try:
        (entry / "error.json").write_text(
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "batchId": entry.name,
                    "createdAt": now_iso_z(),
                    "message": reason,
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return _move_dir(entry, paths.failed)
