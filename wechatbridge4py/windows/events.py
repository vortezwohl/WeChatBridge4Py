"""命名事件信号：纯 ctypes 实现的跨进程「有新批次」通知。

契约：事件只是加速器——消费端启动时必须补扫 Ready，磁盘才是唯一事实源。
命名事件用自动复位（auto-reset）：多次信号合并为一次唤醒是预期行为，
唤醒后的 drain 会重扫全部待处理批次。

本模块保持全平台可导入（bridge 顶部无条件导入它）：非 win32 平台
``_kernel32`` 为 None，signal 静默失败、等待器构造即拒绝。
"""
from __future__ import annotations

import ctypes
import sys

EVENT_NAME = "Local\\WeChatBridge4Py.InboxChanged"

_WAIT_OBJECT_0 = 0

# 仅 win32 存在 ctypes.windll；其他平台导入安全但功能不可用。
_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None


def signal() -> bool:
    """发出一次「收件箱有变化」信号（尽力而为，失败静默返回 False）。"""
    if _kernel32 is None:
        return False
    try:
        handle = _kernel32.CreateEventW(None, False, False, EVENT_NAME)
        if not handle:
            return False
        try:
            return bool(_kernel32.SetEvent(handle))
        finally:
            _kernel32.CloseHandle(handle)
    except Exception:
        return False


class InboxEventWaiter:
    """持有一个命名事件句柄的等待器，供消费循环阻塞等待。

    用法::

        waiter = InboxEventWaiter()
        if waiter.wait(500):   # 500ms 内收到信号
            ...
        waiter.close()
    """

    def __init__(self) -> None:
        if _kernel32 is None:
            raise OSError("命名事件等待仅支持 Windows。")
        self._handle = _kernel32.CreateEventW(None, False, False, EVENT_NAME)
        if not self._handle:
            raise OSError("CreateEventW 失败，无法等待收件箱信号。")

    def wait(self, timeout_ms: int) -> bool:
        """等待信号至多 timeout_ms 毫秒；收到信号返回 True，超时返回 False。"""
        result = _kernel32.WaitForSingleObject(self._handle, int(timeout_ms))
        return result == _WAIT_OBJECT_0

    def close(self) -> None:
        """释放事件句柄（幂等）。"""
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = 0

    def __enter__(self) -> "InboxEventWaiter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
