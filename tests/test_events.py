"""命名事件信号往返验证（仅 Windows）。"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows")


def test_signal_wakes_waiter() -> None:
    from wechatbridge4py.windows.events import InboxEventWaiter, signal

    with InboxEventWaiter() as waiter:
        assert waiter.wait(50) is False  # 先确认超时路径
        assert signal() is True
        assert waiter.wait(1000) is True


def test_waiter_usable_as_context_manager() -> None:
    from wechatbridge4py.windows.events import InboxEventWaiter

    with InboxEventWaiter() as waiter:
        assert waiter.wait(10) is False
