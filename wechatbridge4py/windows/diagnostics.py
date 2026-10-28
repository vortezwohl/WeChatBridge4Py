"""结构化环境体检：返回 DoctorReport，打印与否由调用者决定。

从注册器移出的 doctor 逻辑改写为数据形态——SDK 时代不该替开发者
决定输出方式；``Bridge.doctor()`` 返回报告对象，``report.print()``
才产生人类可读文本。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from wechatbridge4py.windows.registration import (
    RegistrationError,
    dev_mode_enabled,
    find_sdk_tools,
    installed_package,
    share_entry_exe,
)

_OK = "ok"
_WARN = "warn"
_FAIL = "fail"

_STATUS_LABEL = {_OK: "OK", _WARN: "注意", _FAIL: "异常"}


@dataclass(frozen=True)
class Check:
    """一项体检结果。

    Attributes:
        name: 检查项标识（如 "share_exe"、"package"）。
        status: "ok" / "warn" / "fail"；warn 表示可运行但有前提未满足。
        message: 结果描述。
        hint: 可选的处置指引。
    """

    name: str
    status: str
    message: str
    hint: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    """一次体检的完整报告。"""

    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """没有任何 fail 项即视为整体可用。"""
        return all(check.status != _FAIL for check in self.checks)

    def format(self) -> str:
        """渲染为人类可读的多行文本。"""
        lines = []
        for check in self.checks:
            label = _STATUS_LABEL.get(check.status, check.status)
            lines.append(f"[{label}] {check.name}: {check.message}")
            if check.hint:
                lines.append(f"      {check.hint}")
        return "\n".join(lines)

    def print(self) -> None:  # noqa: A003 - 与 str.print 语义冲突可接受
        """打印报告（便捷方法）。"""
        print(self.format())


def run_doctor() -> DoctorReport:
    """执行全部体检项并汇总为报告。"""
    if os.name != "nt":
        return DoctorReport(checks=(
            Check(
                name="platform",
                status=_FAIL,
                message="当前平台不是 Windows：分享入口注册与激活仅支持 win32。",
            ),
        ))

    checks: list[Check] = []
    checks.append(Check(
        name="python",
        status=_OK,
        message=f"Python {sys.version.split()[0]}  prefix={sys.prefix}",
    ))

    try:
        exe = share_entry_exe()
        checks.append(Check(name="share_exe", status=_OK, message=f"分享入口 exe：{exe}"))
    except RegistrationError as error:
        checks.append(Check(
            name="share_exe",
            status=_FAIL,
            message=str(error),
            hint="先在目标虚拟环境安装本包（uv sync / uv pip install -e .）。",
        ))

    package = installed_package()
    if package:
        detail = "，".join(
            f"{key}={package[key]}" for key in package if key != "name"
        )
        checks.append(Check(
            name="package",
            status=_OK,
            message=detail or str(package.get("fullName")),
        ))
    else:
        checks.append(Check(
            name="package",
            status=_WARN,
            message="尚未注册分享入口。",
            hint="调用 bridge.register()（需 AppIdentity）完成注册。",
        ))

    tools = find_sdk_tools()
    missing = [name for name, path in tools.items() if path is None]
    if missing:
        checks.append(Check(
            name="sdk_tools",
            status=_WARN,
            message=f"Windows SDK 工具缺失：{'+'.join(missing)}。",
            hint="注册将回退开发者模式直注路线；安装 Windows SDK 可走签名路线。",
        ))
    else:
        checks.append(Check(
            name="sdk_tools",
            status=_OK,
            message=f"signtool/makeappx/makepri（{tools['signtool'].parent}）",
        ))

    if dev_mode_enabled():
        checks.append(Check(name="dev_mode", status=_OK, message="开发者模式已开启（直注兜底路线的前提）。"))
    else:
        checks.append(Check(
            name="dev_mode",
            status=_WARN,
            message="开发者模式未开启。",
            hint="register 在需要兜底路线时会请求提权自动开启（allow_dev_mode_elevation=False 可禁用）。",
        ))

    from wechatbridge4py.core.inbox import InboxPaths

    paths = InboxPaths()
    try:
        paths.ensure_created()
        checks.append(Check(name="inbox", status=_OK, message=f"收件箱：{paths.root}"))
    except OSError as error:
        checks.append(Check(name="inbox", status=_FAIL, message=f"收件箱不可用：{error}"))

    checks.append(Check(
        name="verify_hint",
        status=_OK,
        message="验证路径：微信多选消息 → 合并转发 → 转发到其他应用。",
        hint="微信菜单不可见时：资源管理器选中 zip → 右键「共享」对照"
             "（可见=注册正常，属微信过滤；不可见=注册问题）；微信转发列表有缓存，"
             "必要时结束微信进程后重开。",
    ))
    return DoctorReport(checks=tuple(checks))
