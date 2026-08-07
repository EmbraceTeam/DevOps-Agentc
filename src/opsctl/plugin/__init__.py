"""Hermes Plugin 注册入口.

被 ``hermes_agent.plugins`` entry point 加载. Plugin 把 opsctl CLI 子命令通过
``ctx.register_tool()`` 暴露给 LLM. handler 永远通过 subprocess 调 ``opsctl --json``,
不直接 import repository (见 spec Design Notes: Plugin ↔ CLI 解耦).

同时注册只读运维技能和 /ops-inspect slash 命令.
"""

from __future__ import annotations

import shlex
from datetime import UTC, datetime
from pathlib import Path

from .schemas import PLUGIN_TOOLS
from .tools import make_handler


def register(ctx) -> None:  # type: ignore[no-untyped-def]
    """Hermes Plugin 注册钩子. ctx 由 Hermes 注入, 提供 register_tool 等 API."""

    # -- 工具注册 --
    for tool_name, spec in PLUGIN_TOOLS.items():
        ctx.register_tool(
            name=tool_name,
            toolset="opsctl",
            schema=spec["schema"],
            handler=make_handler(spec["cli_args"]),
            description=spec["schema"]["description"],
            emoji="🛠️",
        )

    # -- 只读运维技能 (Plugin 打包, 不会被 Agent 自动修改) --
    skill_path = Path(__file__).parent / "skills" / "ops-inspect"
    if skill_path.is_dir():
        try:
            ctx.register_skill(name="ops-inspect", path=skill_path)
        except Exception:
            import logging

            logging.getLogger("opsctl").exception("注册技能 ops-inspect 失败, 继续加载")

    # -- /ops-inspect slash 命令: 统一运维巡检 --
    try:
        ctx.register_command(
            name="ops-inspect",
            description=(
                "统一运维巡检: 检查窗口内 (默认 30 天) 到期的关注点, "
                "按需立即处理/需关注/其余三组报告 (可用 --within 调整窗口)"
            ),
            handler=_handle_inspect,
        )
    except Exception:
        import logging

        logging.getLogger("opsctl").exception("注册命令 /ops-inspect 失败, 继续加载")


# 缺失/未知 severity 与 urgency 的防御权重 (与 repository 排序规则一致)
_SEVERITY_BUCKETS = {"critical", "warning", "info"}


def _parse_window(args: str) -> str:
    """从 args 解析窗口值, 支持 `--within 7d` 与 `--within=7d` 两种形式.

    未指定时默认 ``30d``. 缺值 (裸 ``--within``、空值、值被 flag 吞)、
    shlex 未终止引号、非 str 输入、重复/未知参数一律抛 ValueError
    (由调用方转为巡检失败消息), 绝不静默回退默认窗口.
    """
    if not isinstance(args, str):
        raise ValueError("参数必须是字符串")
    try:
        parts = shlex.split(args)
    except ValueError as exc:
        raise ValueError(f"参数解析失败: {exc}") from exc
    window: str | None = None
    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok == "--within":
            if i + 1 >= len(parts) or parts[i + 1].startswith("-"):
                raise ValueError("--within 缺少值, 用法: --within 7d 或 --within=7d")
            if window is not None:
                raise ValueError("重复指定 --within")
            window = parts[i + 1]
            if not window:
                raise ValueError("--within 缺少值, 用法: --within 7d 或 --within=7d")
            i += 2
        elif tok.startswith("--within="):
            value = tok.split("=", 1)[1]
            if not value:
                raise ValueError("--within 缺少值, 用法: --within 7d 或 --within=7d")
            if window is not None:
                raise ValueError("重复指定 --within")
            window = value
            i += 1
        elif tok.startswith("--"):
            raise ValueError(f"未知参数 {tok!r}, 支持: --within <窗口>")
        else:
            raise ValueError(f"无法识别的参数 {tok!r}, 用法: --within 7d 或 --within=7d")
    return window or "30d"


def _urgency_of_item(item: dict) -> str:
    """缺 urgency 键/非法值按 later 归组 (与 repository.urgency_of 防御一致)."""
    urgency = item.get("urgency")
    return urgency if urgency in ("urgent", "soon", "later") else "later"


def _handle_inspect(args: str) -> str:  # type: ignore[no-untyped-def]
    """/ops-inspect 处理器: 检查窗口内到期的关注点, 按三组汇总报告.

    通过 subprocess 调 `concern due --json --within <窗口>` (与 Plugin tools 一致的
    解耦模式). 组1=🔴 critical|urgent 全量, 组2=🟡 soon非critical 全量,
    组3=🔵 其余折叠一行统计. 条目渲染用 ``name`` (可读资源名, 非 UUID).
    """
    from .tools import _run_opsctl

    try:
        window = _parse_window(args)
    except ValueError as exc:
        return f"巡检失败: {exc}"

    result = _run_opsctl(["concern", "due", "--json", "--within", window])
    if isinstance(result, dict) and "error" in result:
        return f"巡检失败: {result['error']}"
    if not isinstance(result, list):
        return "巡检失败: ops_concerns_due 返回格式异常"
    for item in result:
        if not isinstance(item, dict):
            return "巡检失败: 返回项格式异常"

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    if not result:
        return f"## 统一运维巡检 ({now})\n\n窗口 {window} 内无到期关注项"

    # 三组归集 (组内顺序由数据层排定, 不重排)
    red, yellow, blue = [], [], []
    for item in result:
        urgency = _urgency_of_item(item)
        severity = item.get("severity", "")
        if urgency == "urgent" or severity == "critical":
            red.append(item)
        elif urgency == "soon":
            yellow.append(item)
        else:
            blue.append(item)

    # 头部统计: 未知 severity 计入 other, 共 N = 各桶和
    counts = {"critical": 0, "warning": 0, "info": 0, "other": 0}
    for item in result:
        sev = item.get("severity", "")
        counts[sev if sev in _SEVERITY_BUCKETS else "other"] += 1
    total = len(result)
    stats = ", ".join(f"{k}={v}" for k, v in counts.items())

    def _render(item: dict) -> str:
        name = item.get("name") or item.get("resource", "?")
        desc = item.get("desc", item.get("description", "?"))
        due = item.get("due", "")
        part = f"- {name} — {desc}"
        if due:
            part += f" — 到期: {due}"
        return part

    lines = [
        f"## 统一运维巡检 ({now})",
        "",
        f"**窗口**: {window} 内到期关注点",
        f"**统计**: {stats}, 共 {total}",
        "",
    ]
    if red:
        lines.append(f"🔴 需立即处理 ({len(red)})")
        lines.extend(_render(c) for c in red)
        lines.append("")
    if yellow:
        lines.append(f"🟡 需关注 ({len(yellow)})")
        lines.extend(_render(c) for c in yellow)
        lines.append("")
    if blue:
        lines.append(f"🔵 其余 {len(blue)} 项 (折叠)")

    return "\n".join(lines)
