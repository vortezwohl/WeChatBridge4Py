"""分享入口主函数：被系统以 Share Target 契约激活的 wcb4py-share.exe 入口。

进程生命周期极短：读激活参数 → 校验文件 → 暂存 → 原子提交 → 发信号 →
向系统报告完成 → 退出。本进程不做任何投递，也不知道任何目标应用——
那是消费端开发者回调里的职责。

关键实现约束（与上游项目实测结论一致）：
- 必须用 OS 自带的 ``Windows.ApplicationModel.AppInstance`` 读取激活参数；
  稀疏包进程加载 WindowsAppSDK 会直接 REGDB_E_CLASSNOTREG 崩溃；
- 每个属性/方法都要容忍失败：本入口可能被手动运行（无激活参数）、被
  非分享方式激活，任何情况下都要干净退出而不是崩溃弹窗。
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import sys
from typing import Sequence

from wechatbridge4py.core.inbox import InboxError, InboxPaths, InboxWriter, SourceFile

_APPMODEL_ERROR_NO_PACKAGE = 15700


def main(argv: Sequence[str] | None = None) -> int:
    """gui_scripts 入口：wcb4py-share.exe 的主函数。

    Args:
        argv: 命令行参数；``--registration-check`` 走身份探针而非分享处理。

    Returns:
        进程退出码（0 正常；1 失败。分享处理失败也已尽力报告给系统）。
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    paths = InboxPaths()
    paths.ensure_created()
    if "--registration-check" in arguments:
        return _print_identity()
    try:
        asyncio.run(_relay_share(paths))
        return 0
    except Exception as error:  # 兜底：入口进程绝不允许带异常弹窗退出
        paths.log("分享入口主流程异常", error)
        return 1


async def _relay_share(paths: InboxPaths) -> None:
    """消费一次 Share Target 激活：提取 StorageItems → 落盘 → 报告。"""
    from winrt.windows.applicationmodel import AppInstance
    from winrt.windows.applicationmodel.activation import (
        ActivationKind,
        ShareTargetActivatedEventArgs,
    )
    from winrt.windows.applicationmodel.datatransfer import StandardDataFormats

    try:
        activated = AppInstance.get_activated_event_args()
    except Exception as error:
        paths.log("读取激活参数失败（未打包运行？）", error)
        return
    if activated is None:
        paths.log("没有激活参数：本入口需要由系统分享契约调起。")
        return
    try:
        kind = activated.kind
    except Exception:
        kind = None
    if kind != ActivationKind.SHARE_TARGET:
        paths.log(f"收到非分享激活：{kind}")
        return

    share_args = ShareTargetActivatedEventArgs._from(activated)
    operation = share_args.share_operation
    operation.report_started()
    paths.log("[trace] share.activated")
    try:
        data = operation.data
        if not data.contains(StandardDataFormats.STORAGE_ITEMS):
            raise InboxError("只接收文件型分享（StorageItems）。")
        storage_items = await data.get_storage_items_async()
        sources: list[SourceFile] = []
        for index, item in enumerate(storage_items):
            path = getattr(item, "path", None)
            if not path:
                name = getattr(item, "name", "<未知>")
                raise InboxError(f"分享项没有可读取的路径：{name}")
            content_type = getattr(item, "content_type", None)
            sources.append(
                SourceFile(
                    path=str(path),
                    display_name=str(getattr(item, "name", None) or path),
                    content_type=str(content_type) if content_type else None,
                    item_index=index,
                )
            )
        paths.log(f"[trace] share.sources count={len(sources)}")

        writer = InboxWriter(paths)
        staged = writer.stage(sources)
        paths.log(f"[trace] share.staged batch={staged.batch_id}")
        ready_dir = writer.commit(staged)
        paths.log(f"[trace] share.committed batch={staged.batch_id} dir={ready_dir}")

        from wechatbridge4py.win.events import signal

        signal()
        operation.report_completed()
        paths.log(f"分享批次已提交：{staged.batch_id}")
    except Exception as error:
        paths.log("分享处理失败", error)
        try:
            operation.report_error(str(error))
        except Exception:
            pass


def _print_identity() -> int:
    """身份探针：报告当前进程是否持有稀疏包身份（doctor/排查用）。"""
    buffer = ctypes.create_unicode_buffer(1024)
    size = ctypes.c_uint32(len(buffer))
    code = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(size), buffer)
    identity = buffer.value if code == 0 else None
    print(
        json.dumps(
            {
                "hasPackageIdentity": code == 0,
                "packageFullName": identity,
                "errorCode": None if code == 0 else code,
                "noPackage": code == _APPMODEL_ERROR_NO_PACKAGE,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
