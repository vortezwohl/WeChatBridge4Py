"""wcb4py 命令行工具：注册/注销分享入口、环境体检、免微信的演示注入。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wechatbridge4py import __version__


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。

    Args:
        argv: 命令行参数；缺省取 sys.argv[1:]。

    Returns:
        进程退出码。
    """
    parser = argparse.ArgumentParser(
        prog="wcb4py",
        description="WeChatBridge4Py：微信「合并转发」→ Python 回调的桥接工具。",
    )
    parser.add_argument("--version", action="version", version=f"wcb4py {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("register", help="注册 Windows 分享入口（需开发者模式）")
    subparsers.add_parser("unregister", help="注销分享入口")
    subparsers.add_parser("doctor", help="环境体检：入口 exe、注册状态、开发者模式、收件箱")

    demo = subparsers.add_parser(
        "demo-share", help="把 zip 模拟成一次微信分享注入收件箱（无需微信，用于调试回调）"
    )
    demo.add_argument("files", nargs="+", help="要注入的 .zip 文件路径（可多个）")

    args = parser.parse_args(argv)

    if args.command in {"register", "unregister", "doctor"} and sys.platform != "win32":
        print(f"`wcb4py {args.command}` 仅支持 Windows。")
        return 2

    if args.command == "register":
        from wechatbridge4py.win.registration import register

        strategy = register()
        print(f"注册成功（策略：{strategy}）。现在可在微信「转发到其他应用」里选择「聊天转发桥」。")
        if strategy == "layout-copy":
            print("注意：当前使用布局目录策略，重新安装/升级本包后需再次执行 wcb4py register。")
        return 0

    if args.command == "unregister":
        from wechatbridge4py.win.registration import unregister

        removed = unregister()
        print("已注销分享入口。" if removed else "当前没有已注册的分享入口。")
        return 0

    if args.command == "doctor":
        from wechatbridge4py.win.registration import doctor

        for line in doctor():
            print(line)
        return 0

    if args.command == "demo-share":
        from wechatbridge4py.core.inbox import InboxPaths, InboxWriter, SourceFile
        from wechatbridge4py.win.events import signal

        paths = InboxPaths()
        sources = []
        for index, name in enumerate(args.files):
            candidate = Path(name)
            if not candidate.exists():
                print(f"文件不存在：{candidate}")
                return 2
            sources.append(
                SourceFile(
                    path=str(candidate.resolve()),
                    display_name=candidate.name,
                    item_index=index,
                )
            )
        writer = InboxWriter(paths)
        staged = writer.stage(sources)
        writer.commit(staged)
        signal()
        print(f"已注入批次 {staged.batch_id}")
        print(f"收件箱：{paths.ready / staged.batch_id}")
        print("启动你的 Bridge（bridge.run_forever()）或调用 bridge.drain() 即可收到回调。")
        return 0

    return 2  # argparse 已保证 command 必填，此行仅为类型完备


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
