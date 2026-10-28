"""Bridge：SDK 唯一编排对象——消费、注册、体检、调试注入。

Bridge 不实现任何机制，只把 core 的收件箱协议、windows 的注册/激活/
诊断按开发者视角编排成一个对象：

- 消费：``on_share`` 注册回调，``run_forever``/``drain`` 驱动分发
  （启动即补扫、每批恰好一次、至少一次语义）；
- 注册：``register``（需要 ``app`` 身份）/ ``unregister``；
- 体检：``doctor`` 返回结构化报告；
- 调试：``inject`` 把 zip 模拟成一次分享，无需微信。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Callable

from wechatbridge4py.core.archive import ArchiveError, read_archive
from wechatbridge4py.core.inbox import (
    MANIFEST_NAME,
    SCHEMA_VERSION,
    InboxPaths,
    InboxWriter,
    SourceFile,
    now_iso_z,
    parse_iso_z,
)
from wechatbridge4py.core.models import ShareBatch, SharedItem, Transcript
from wechatbridge4py.identity import AppIdentity

ShareHandler = Callable[[ShareBatch], object]


class Bridge:
    """收件箱消费与分享入口注册的编排器。

    Args:
        app: 应用身份（显示名 + logo）。``None`` 表示纯消费模式：
            doctor / inject / on_share / run_forever 可用，register 会拒绝。
        inbox: 收件箱根目录或 :class:`InboxPaths`；缺省用环境变量/系统默认。
        poll_interval: 无信号平台的轮询周期（秒），Windows 上仅作兜底。
    """

    def __init__(
        self,
        app: AppIdentity | None = None,
        inbox: str | os.PathLike[str] | InboxPaths | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self.app = app
        self.paths = inbox if isinstance(inbox, InboxPaths) else InboxPaths(inbox)
        self.poll_interval = poll_interval
        self._handlers: list[ShareHandler] = []

    # ------------------------------------------------------------------
    # 消费
    # ------------------------------------------------------------------

    @property
    def inbox(self) -> InboxPaths:
        """收件箱布局（只读视图）。"""
        return self.paths

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
                new_dir = _quarantine(
                    entry, self.paths, f"回调异常：{error.__class__.__name__}: {error}"
                )
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

    # ------------------------------------------------------------------
    # 注册与诊断（SDK-only：原 CLI 的全部归宿）
    # ------------------------------------------------------------------

    def register(self, *, allow_dev_mode_elevation: bool = True):
        """以 ``app`` 身份注册 Windows 分享入口（幂等）。

        Args:
            allow_dev_mode_elevation: 进入兜底直注路线前，若开发者模式
                未开启，是否允许弹 UAC 自动开启。

        Returns:
            RegisterResult：成功路线（signed-sparse / layout-copy）与包全名。

        Raises:
            ValueError: 纯消费模式（构造时未提供 ``app``）。
            RuntimeError: 非 Windows 平台。
        """
        if self.app is None:
            raise ValueError(
                "注册分享入口需要应用身份：Bridge(app=AppIdentity(name=..., logo=...))。"
            )
        if sys.platform != "win32":
            raise RuntimeError("register 仅支持 Windows。")
        from wechatbridge4py.windows.registration import register

        return register(self.app, allow_dev_mode_elevation=allow_dev_mode_elevation)

    def unregister(self) -> bool:
        """注销分享入口（未注册时返回 False）。"""
        if sys.platform != "win32":
            raise RuntimeError("unregister 仅支持 Windows。")
        from wechatbridge4py.windows.registration import unregister

        return unregister()

    def doctor(self):
        """环境体检。

        Returns:
            DoctorReport：结构化结果，``report.ok`` 判定整体可用，
            ``report.print()`` 输出人类可读文本。
        """
        from wechatbridge4py.windows.diagnostics import run_doctor

        return run_doctor()

    def inject(self, *zip_paths: str | os.PathLike[str]) -> str:
        """把一个或多个 zip 模拟成一次微信分享，注入收件箱（无需微信）。

        一次调用 = 一次模拟分享：全部文件进入同一个批次，随后由
        run_forever / drain 分发给回调。

        Args:
            zip_paths: 要注入的 .zip 文件路径。

        Returns:
            注入批次的 id。

        Raises:
            FileNotFoundError: 任一路径不存在。
        """
        sources: list[SourceFile] = []
        for index, raw in enumerate(zip_paths):
            path = Path(raw)
            if not path.exists():
                raise FileNotFoundError(f"要注入的文件不存在：{path}")
            sources.append(
                SourceFile(path=str(path.resolve()), display_name=path.name, item_index=index)
            )
        writer = InboxWriter(self.paths)
        staged = writer.stage(sources)
        writer.commit(staged)
        if sys.platform == "win32":
            try:
                from wechatbridge4py.windows.events import signal

                signal()
            except Exception:
                pass  # 信号只是加速器：drain 的补扫兜底
        self.paths.log(f"[trace] injected batch={staged.batch_id}")
        return staged.batch_id

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

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
                transcripts=transcripts,
                parse_errors=parse_errors,
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def _open_waiter():
        """Windows 上打开命名事件等待器；其他平台/失败时返回 None 走轮询。"""
        if sys.platform == "win32":
            try:
                from wechatbridge4py.windows.events import InboxEventWaiter

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
