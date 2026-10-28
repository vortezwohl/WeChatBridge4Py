"""收件箱文件协议：批次暂存、原子发布与失败落档。

本模块定义 WeChatBridge4Py 的跨进程数据契约。分享入口进程（被 Share Target
激活的 wcb4py-share.exe）把微信合并转发的文件写入 Staging，全部就绪后用一次
目录改名（rename）原子发布到 Ready；消费进程（Bridge）只认 Ready。目录约定：

    <root>/Staging/<batch-id>.staging/files/<安全文件名> + manifest.json
    <root>/Ready/<batch-id>/manifest.json + files/...   ← 原子发布后的可见批次
    <root>/Done/<batch-id>/...                          ← 消费成功后由 Bridge 移入
    <root>/Failed/<batch-id>... 与 Failed/<batch-id>.json ← 校验/回调失败
    <root>/Logs/relay.log                               ← 两侧共用的文本日志

协议要点（继承上游 WeChatBridge 项目验证过的思想）：
- 磁盘是唯一事实源，命名事件只是加速器；
- 对消费者可见即完整：Staging → Ready 的单次目录改名即原子提交；
- 任何一个文件校验失败则整批作废，绝不发布半批。
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
FAILURE_SUFFIX = ".json"

# 与上游项目一致的保守上限：单文件 1 GiB、整批 1 GiB。
MAX_FILE_BYTES = 1 << 30
MAX_BATCH_BYTES = 1 << 30

# Windows 文件属性位：符号链接 / 重解析点（不允许进入收件箱）。
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class InboxError(RuntimeError):
    """收件箱写入或校验失败。消息面向开发者，可直接展示。"""


def now_iso_z() -> str:
    """当前 UTC 时间，格式化为两端互通的 ISO-8601 Z 形式。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_z(text: str) -> datetime:
    """解析 manifest 里的 ISO-8601 Z 时间戳为带时区的 datetime。"""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@dataclass(frozen=True)
class SourceFile:
    """进入暂存之前的一个分享文件。

    Attributes:
        path: 来源绝对路径（微信放在临时目录里的真实文件）。
        display_name: 分享面板给出的展示名，作为收件箱内的文件名基准。
        content_type: 来源 MIME 类型（可能为空）。
        item_index / attachment_index: 保留宿主遍历顺序，便于排查。
    """

    path: str
    display_name: str
    content_type: str | None = None
    item_index: int = 0
    attachment_index: int = 0


@dataclass(frozen=True)
class SharedItem:
    """manifest.json 里登记的一个批次文件。

    Attributes:
        id: 条目唯一标识。
        display_name: 展示名（微信原始文件名）。
        relative_path: 相对批次目录的 POSIX 风格路径（files/<安全文件名>）。
        path: 消费端加载时填充的绝对路径；写入端不填。
    """

    id: str
    display_name: str
    relative_path: str
    content_type: str | None
    byte_count: int
    item_index: int
    attachment_index: int
    path: str | None = None


@dataclass(frozen=True)
class StagedShare:
    """已完整暂存、尚未发布的批次（半提交状态）。"""

    batch_id: str
    staging_dir: Path
    ready_dir: Path
    manifest: dict


class InboxPaths:
    """收件箱目录布局与基础维护。

    Args:
        root: 根目录。缺省依次取环境变量 WECHATBRIDGE4PY_INBOX、
            %LOCALAPPDATA%\\WeChatBridge4Py\\Inbox、~/WeChatBridge4Py/Inbox。
    """

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        if root is None:
            env = os.environ.get("WECHATBRIDGE4PY_INBOX")
            root = env if env else None
        if root is None:
            base = os.environ.get("LOCALAPPDATA") or str(Path.home())
            root = str(Path(base) / "WeChatBridge4Py" / "Inbox")
        self.root = Path(root)

    @property
    def staging(self) -> Path:
        return self.root / "Staging"

    @property
    def ready(self) -> Path:
        return self.root / "Ready"

    @property
    def done(self) -> Path:
        return self.root / "Done"

    @property
    def failed(self) -> Path:
        return self.root / "Failed"

    @property
    def logs(self) -> Path:
        return self.root / "Logs"

    def ensure_created(self) -> None:
        """创建全部约定目录（幂等）。"""
        for directory in (self.staging, self.ready, self.done, self.failed, self.logs):
            directory.mkdir(parents=True, exist_ok=True)

    def log(self, message: str, exc: BaseException | None = None) -> None:
        """追加一行结构化日志到 Logs/relay.log；日志失败绝不影响主流程。"""
        try:
            self.ensure_created()
            line = f"{now_iso_z()} {message}" + (f" | {exc!r}" if exc else "")
            with (self.logs / "relay.log").open("a", encoding="utf-8", errors="replace") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def prune_staging(self, older_than_seconds: float = 1800.0) -> int:
        """清理显然已无人写入的暂存残骸。

        以整棵目录树内最新的 mtime 计龄（大文件拷贝期间只有正在写的文件
        在更新时间戳，按目录时间计龄会误杀进行中的分享），默认 30 分钟。
        """
        import time

        if not self.staging.exists():
            return 0
        removed = 0
        deadline = time.time() - older_than_seconds
        for candidate in list(self.staging.iterdir()):
            newest = _newest_mtime(candidate)
            if newest is None or newest > deadline:
                continue
            try:
                if candidate.is_dir():
                    shutil.rmtree(candidate, ignore_errors=True)
                else:
                    candidate.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        return removed


def _newest_mtime(path: Path) -> float | None:
    """目录树内最新的 mtime；任何访问异常按 None 处理。"""
    newest: float | None = None
    try:
        newest = path.stat().st_mtime
        if path.is_dir():
            for child in path.rglob("*"):
                try:
                    stamp = child.stat().st_mtime
                except OSError:
                    continue
                if newest is None or stamp > newest:
                    newest = stamp
    except OSError:
        return None
    return newest


class InboxWriter:
    """分享入口侧的写入器：校验 → 暂存 → 原子发布。"""

    def __init__(self, paths: InboxPaths | None = None) -> None:
        self.paths = paths or InboxPaths()

    def stage(self, sources: list[SourceFile], *, action: str = "relay") -> StagedShare:
        """校验并复制全部来源文件到 Staging，写好 manifest（不发布）。

        Args:
            sources: 分享携带的全部文件；为空即失败。
            action: 批次来源动作标识，仅用于展示与排查。

        Returns:
            暂存完成的半提交批次，交给 :meth:`commit` 发布。

        Raises:
            InboxError: 任一文件不合规（非 zip、符号链接、超限、不可读等）。
                此时整批作废，残骸清除，失败原因写入 Failed/<batch-id>.json。
        """
        if not sources:
            raise InboxError("分享中没有可处理的文件。")
        self.paths.ensure_created()
        batch_id = str(uuid.uuid4())
        staging_dir = self.paths.staging / f"{batch_id}.staging"
        ready_dir = self.paths.ready / batch_id
        files_dir = staging_dir / "files"
        used_names: set[str] = set()
        total_bytes = 0
        items: list[dict] = []
        try:
            files_dir.mkdir(parents=True)
            for source in sources:
                display = source.display_name or Path(source.path).name
                _validate_source(source, display)
                size = Path(source.path).stat().st_size
                total_bytes += size
                if total_bytes > MAX_BATCH_BYTES:
                    raise InboxError("分享批次过大（超过 1 GiB）。")
                safe_name = _unique_name(_sanitize_file_name(display), used_names)
                destination = files_dir / safe_name
                shutil.copyfile(source.path, destination)
                items.append({
                    "id": str(uuid.uuid4()),
                    "displayName": display,
                    "relativePath": f"files/{safe_name}",
                    "contentType": source.content_type,
                    "byteCount": size,
                    "itemIndex": source.item_index,
                    "attachmentIndex": source.attachment_index,
                })
            manifest = {
                "schemaVersion": SCHEMA_VERSION,
                "batchId": batch_id,
                "createdAt": now_iso_z(),
                "action": action,
                "items": items,
            }
            (staging_dir / MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return StagedShare(batch_id, staging_dir, ready_dir, manifest)
        except Exception as error:
            shutil.rmtree(staging_dir, ignore_errors=True)
            self._write_failure(batch_id, str(error))
            raise

    def commit(self, staged: StagedShare) -> Path:
        """把暂存批次原子发布到 Ready（单次目录改名），返回 Ready 目录。

        Raises:
            InboxError: 改名失败（如目标被占用）。
        """
        try:
            os.rename(staged.staging_dir, staged.ready_dir)
        except OSError as error:
            shutil.rmtree(staged.staging_dir, ignore_errors=True)
            self._write_failure(staged.batch_id, f"发布失败：{error}")
            raise InboxError(f"批次发布失败：{error}") from error
        return staged.ready_dir

    def _write_failure(self, batch_id: str, message: str) -> None:
        """尽力把失败原因落档到 Failed/<batch-id>.json，供消费端转述。"""
        try:
            self.paths.ensure_created()
            payload = {
                "schemaVersion": SCHEMA_VERSION,
                "batchId": batch_id,
                "createdAt": now_iso_z(),
                "message": message,
            }
            (self.paths.failed / f"{batch_id}{FAILURE_SUFFIX}").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass


def _validate_source(source: SourceFile, display: str) -> None:
    """单文件准入校验：存在、普通文件、非重解析点、zip 后缀、不超单文件上限。"""
    path = Path(source.path)
    if not path.exists():
        raise InboxError(f"找不到分享文件：{display}")
    if not path.is_file():
        raise InboxError(f"分享项不是文件：{display}")
    attributes = getattr(path.stat(), "st_file_attributes", 0)
    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise InboxError(f"不接受符号链接或重解析点：{display}")
    if not display.lower().endswith(".zip"):
        raise InboxError(f"只接收微信导出的 .zip 归档：{display}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise InboxError(f"文件过大（超过 1 GiB）：{display}")


def _sanitize_file_name(name: str) -> str:
    """清洗文件名为安全的 Windows 叶子名；清洗后为空视为非法。"""
    leaf = Path(name).name
    if any(ord(ch) < 32 for ch in leaf):
        raise InboxError(f"分享文件名包含控制字符：{name!r}")
    invalid = set('<>:"/\\|?*')
    sanitized = "".join("_" if ch in invalid else ch for ch in leaf).strip()
    if not sanitized or sanitized in {".", ".."}:
        raise InboxError(f"分享文件名不安全：{name!r}")
    return sanitized


def _unique_name(name: str, used: set[str]) -> str:
    """同批同名文件自动追加 (2)、(3) 序号，避免互相覆盖。"""
    if name not in used:
        used.add(name)
        return name
    stem, dot, suffix = name.rpartition(".")
    index = 2
    while True:
        candidate = f"{stem} ({index}).{suffix}" if dot else f"{name} ({index})"
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1
