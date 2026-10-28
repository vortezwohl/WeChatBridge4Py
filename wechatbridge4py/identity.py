"""应用身份：开发者自定义的分享入口显示名与 logo。

名字与图标是产品契约而非 Windows 实现细节，因此本模块放在平台无关层。
构造即校验——显示名的微信过滤红线（不得含连续 微信/WeChat/Weixin
子串，上游项目实测结论）与 logo 的形态约束都在这里尽早暴露。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wechatbridge4py.core.imaging import ImageError, decode_png

# 微信宿主会隐藏显示名里含这些连续子串的分享目标（上游实测结论）。
_FORBIDDEN_SUBSTRINGS = ("微信", "WeChat", "Weixin")

# 分享面板显示名的合理上限，超长会被系统截断。
MAX_NAME_LENGTH = 40

# 清单需要的最小图标档位；logo 短边必须不低于它，保证 150 档不升采样。
MIN_LOGO_SIDE = 150


class AppIdentityError(ValueError):
    """应用身份不合规。消息面向开发者，可直接展示。"""


@dataclass(frozen=True)
class AppIdentity:
    """分享入口的应用身份（显示名 + logo）。

    Attributes:
        name: 微信「转发到其他应用」面板里显示的名字。
        logo: PNG 源图；接受路径（str/Path）或原始字节。要求 8 位深、
            非隔行、RGB/RGBA，短边 ≥ 150。SDK 负责生成 44/150 两档图标
            （等比缩放 + 透明补边，不拉伸变形）。

    Raises:
        AppIdentityError: 名字为空/超长/含微信过滤红线子串，
            或 logo 不存在、不是受支持的 PNG、尺寸不足。
    """

    name: str
    logo: Path | str | bytes

    def __post_init__(self) -> None:
        stripped = self.name.strip()
        if not stripped:
            raise AppIdentityError("应用名不能为空。")
        if len(stripped) > MAX_NAME_LENGTH:
            raise AppIdentityError(f"应用名过长（{len(stripped)} > {MAX_NAME_LENGTH} 字符）。")
        folded = stripped.lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            if forbidden.lower() in folded:
                raise AppIdentityError(
                    f"应用名不能包含连续的「{forbidden}」子串："
                    "微信宿主会隐藏这类分享目标（上游项目实测结论）。"
                )
        image = self._decode()
        if min(image.width, image.height) < MIN_LOGO_SIDE:
            raise AppIdentityError(
                f"logo 短边至少需要 {MIN_LOGO_SIDE}px（当前 {image.width}×{image.height}），"
                "否则分享图标会模糊。"
            )

    def logo_bytes(self) -> bytes:
        """返回 logo 的原始字节（register 时由注册器再次消费）。"""
        if isinstance(self.logo, bytes):
            return self.logo
        return Path(self.logo).read_bytes()

    def _decode(self):
        """构造期校验用：读取并解码 logo，形态错误转为 AppIdentityError。"""
        try:
            data = self.logo_bytes()
        except OSError as error:
            raise AppIdentityError(f"logo 文件无法读取：{error}") from error
        try:
            return decode_png(data)
        except ImageError as error:
            raise AppIdentityError(f"logo 不是受支持的 PNG：{error}") from error
