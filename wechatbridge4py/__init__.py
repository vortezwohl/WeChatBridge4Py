"""WeChatBridge4Py：微信「合并转发」→ Python 回调的桥接基础设施。

本包面向开发者，不预设任何目标应用（不投递 Codex/Claude，不写 Obsidian）。
它只做一件事：把用户在微信里「合并转发」导出的聊天归档，安全地送达你的
Python 进程，并以结构化消息流交给你的回调。投递到哪个应用、如何处理，
完全由你的代码决定。

典型用法（消费端）::

    from wechatbridge4py import Bridge

    bridge = Bridge()

    @bridge.on_share
    def on_share(batch):
        for message in batch.all_messages:
            print(message.time, message.sender, message.text)

    bridge.run_forever()

Windows 入口链路：
微信多选消息 → 合并转发 → 系统分享面板选「聊天转发桥」→
wcb4py-share.exe 被 Share Target 激活 → 文件原子落盘收件箱 →
命名事件唤醒 Bridge → 调用开发者的 on_share 回调。

core 子包保持零第三方依赖且跨平台，为未来 macOS 端复用同一套文件契约。
"""
from wechatbridge4py.bridge import Bridge, ShareBatch
from wechatbridge4py.core.archive import Transcript
from wechatbridge4py.core.inbox import InboxPaths, SharedItem, SourceFile
from wechatbridge4py.core.transcript import Message

__version__ = "0.1.0"

__all__ = [
    "Bridge",
    "ShareBatch",
    "Transcript",
    "Message",
    "InboxPaths",
    "SharedItem",
    "SourceFile",
]
