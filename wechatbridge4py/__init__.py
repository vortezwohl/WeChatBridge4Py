"""WeChatBridge4Py SDK：微信「合并转发」→ Python 回调的桥接基础设施。

本包是纯 SDK（无 CLI），一切操作经由 :class:`Bridge`。开发者以自己的
应用身份（显示名 + logo）注册分享入口，微信里「合并转发 → 转发到其他
应用」选你的入口后，聊天归档即以结构化消息流送达回调。本包不预设任何
目标应用——发到哪里、怎么处理，完全由你的代码决定。

典型用法::

    from wechatbridge4py import AppIdentity, Bridge

    bridge = Bridge(
        app=AppIdentity(
            name="我的笔记助手",      # 微信分享面板里显示的名字
            logo="assets/logo.png",  # 你的 logo（PNG，短边 ≥150）
        ),
    )
    bridge.register()
    bridge.doctor().print()

    @bridge.on_share
    def on_share(batch):
        for message in batch.all_messages:
            print(f"[{message.time}] {message.sender}: {message.text}")

    bridge.run_forever()          # 或 bridge.inject("聊天记录.zip") 免微信调试

链路：微信多选消息 → 合并转发 → 系统分享面板选你的入口 →
wcb4py-share.exe 被 Share Target 激活（无 UI，秒退）→ 文件原子落盘
收件箱 → 命名事件唤醒 Bridge → 调用你的回调。

结构：``core`` 为平台无关契约层（零第三方依赖，macOS 复用）；
``windows`` 为 Windows 实现（激活入口、事件、注册、诊断）；
本文件是唯一的公开 facade——除此之外的任何导入都属实现细节。
"""
from wechatbridge4py.bridge import Bridge
from wechatbridge4py.core.inbox import InboxPaths
from wechatbridge4py.core.models import Message, ShareBatch, SharedItem, Transcript
from wechatbridge4py.identity import AppIdentity, AppIdentityError
from wechatbridge4py.windows.diagnostics import DoctorReport
from wechatbridge4py.windows.registration import RegisterResult

__version__ = "0.2.0"

__all__ = [
    "AppIdentity",
    "AppIdentityError",
    "Bridge",
    "DoctorReport",
    "InboxPaths",
    "Message",
    "RegisterResult",
    "ShareBatch",
    "SharedItem",
    "Transcript",
]
