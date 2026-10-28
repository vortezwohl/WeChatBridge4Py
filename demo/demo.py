"""WeChatBridge4Py 使用示例：注册分享入口，并在控制台输出微信转发的消息。

运行方式::

    uv run python demo/demo.py

流程：以自定义应用身份注册分享入口（幂等，仅需首次授权）→ 环境体检 →
阻塞监听。之后在微信里多选消息 → 合并转发 → 转发到其他应用 →
选「ChatBridge Demo」，消息随即打印在本控制台。Ctrl+C 退出。

没有微信环境也能验证接收端：另开一个终端执行::

    uv run python -c "from wechatbridge4py import Bridge; Bridge().inject('某个聊天记录.zip')"
"""
from __future__ import annotations

import sys
from pathlib import Path

from wechatbridge4py import AppIdentity, Bridge, ShareBatch
from wechatbridge4py.core.imaging import Image, encode_png
from wechatbridge4py.windows.registration import RegistrationError

APP_NAME = "ChatBridge Demo"
LOGO_PATH = Path(__file__).with_name("logo.png")


def ensure_logo() -> Path:
    """确保 demo logo 存在：首次运行时用 SDK 自带的 imaging 生成一张渐变图。

    Returns:
        logo 文件路径（缓存于 demo/logo.png，不入库）。
    """
    if LOGO_PATH.exists():
        return LOGO_PATH
    size = 256
    radius = 42
    center = size // 2
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            # 深青 → 亮青的对角渐变底，中心一枚白色圆点当作品牌记号
            red = (x * 30) // (size - 1)
            green = 96 + (x + y) * 90 // (2 * (size - 1))
            blue = 88 + (y * 60) // (size - 1)
            if (x - center) ** 2 + (y - center) ** 2 <= radius ** 2:
                red, green, blue = 255, 255, 255
            pixels += bytes((red, green, blue, 255))
    LOGO_PATH.write_bytes(
        encode_png(Image(width=size, height=size, pixels=bytes(pixels)))
    )
    return LOGO_PATH


def on_share(batch: ShareBatch) -> None:
    """收到一次转发：打印批次摘要与全部消息（多行正文缩进展示）。"""
    print()
    print("=" * 64)
    print(f"收到分享批次 {batch.batch_id}（{len(batch.items)} 个文件）")
    for error in batch.parse_errors:
        print(f"  [解析失败] {error}")
    for transcript in batch.transcripts:
        attachments = len(transcript.attachment_names)
        suffix = f"，{attachments} 个附件" if attachments else ""
        summary = f"── 归档 {transcript.zip_path.name}（{len(transcript.messages)} 条消息"
        print(f"{summary}{suffix}）")
    for message in batch.all_messages:
        stamp = message.time.strftime("%Y-%m-%d %H:%M")
        body = message.text.replace("\n", "\n        ")
        print(f"  [{stamp}] {message.sender}: {body}")
    print("=" * 64)


def main() -> int:
    """demo 主流程：注册 → 体检 → 监听（Ctrl+C 干净退出）。"""
    if sys.platform != "win32":
        print("本 demo 需要在 Windows 上运行（分享入口仅支持 Windows）。")
        return 2

    bridge = Bridge(app=AppIdentity(name=APP_NAME, logo=str(ensure_logo())))

    print(f"[1/3] 以「{APP_NAME}」身份注册分享入口（幂等）…")
    try:
        result = bridge.register()
        print(f"      注册成功：{result.strategy}（{result.package_full_name}）")
    except RegistrationError as error:
        print(f"      [警告] 注册失败，继续监听已有入口：{error}")

    print("[2/3] 环境体检：")
    bridge.doctor().print()

    bridge.on_share(on_share)
    print()
    print("[3/3] 开始监听。请在微信里：多选消息 → 合并转发 → 转发到其他应用")
    print(f"      → 选「{APP_NAME}」，消息会打印在这里。Ctrl+C 退出。")
    bridge.run_forever()
    print("已退出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
