"""Hermes Plugin 注册入口.

被 ``hermes_agent.plugins`` entry point 加载. Plugin 把 opsctl CLI 子命令通过
``ctx.register_tool()`` 暴露给 LLM. handler 永远通过 subprocess 调 ``opsctl --json``,
不直接 import repository (见 spec Design Notes: Plugin ↔ CLI 解耦).

同时注册只读运维技能和 /ops-inspect slash 命令.
"""

from __future__ import annotations

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
            description="统一运维巡检: 遍历所有资源并检查 7 天内到期的关注点",
            handler=_handle_inspect,
        )
    except Exception:
        import logging

        logging.getLogger("opsctl").exception("注册命令 /ops-inspect 失败, 继续加载")


def _handle_inspect(args: str) -> str:  # type: ignore[no-untyped-def]
    """/ops-inspect 处理器: 遍历每个资源, 逐个检查其关注项, 汇总报告.

    通过 subprocess 直接调 opsctl CLI (与 Plugin tools 一致的解耦模式).
    """
    from .tools import _run_opsctl

    # 1. 获取资源列表
    resources_result = _run_opsctl(["resource", "list", "--json"])
    if isinstance(resources_result, dict) and "error" in resources_result:
        return f"巡检失败: 无法获取资源列表 — {resources_result['error']}"
    if not isinstance(resources_result, list):
        return "巡检失败: ops_list_resources 返回格式异常"

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"## 统一运维巡检 ({now})", ""]

    # 资源概况
    type_counts: dict[str, int] = {}
    for r in resources_result:
        t = r.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    lines.append(f"**资源总数**: {len(resources_result)}")
    for t, c in sorted(type_counts.items()):
        lines.append(f"  - {t}: {c}")
    lines.append("")

    # 2. 遍历每个资源, 逐个检查关注项
    all_concerns: list[dict] = []
    for r in resources_result:
        rname = r.get("name", "?")
        result = _run_opsctl(["concern", "list", "--json", "--resource", rname])
        if isinstance(result, list):
            for c in result:
                c["_resource_name"] = rname
                all_concerns.append(c)

    # 3. 按 severity 分级输出
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    sorted_concerns = sorted(
        all_concerns,
        key=lambda c: (severity_order.get(c.get("severity", "info"), 99), c.get("due", "")),
    )

    counts = {"critical": 0, "warning": 0, "info": 0}
    for c in sorted_concerns:
        sev = c.get("severity", "info")
        counts.setdefault(sev, 0)
        counts[sev] += 1

    for sev_label, sev_key in [("Critical", "critical"), ("Warning", "warning"), ("Info", "info")]:
        items = [c for c in sorted_concerns if c.get("severity", "info") == sev_key]
        if not items:
            continue
        emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(sev_key, "")
        lines.append(f"### {emoji} {sev_label}")
        for c in items:
            resource = c.get("_resource_name", c.get("resource", "?"))
            desc = c.get("desc", c.get("description", "?"))
            due = c.get("due", "")
            part = f"- [{resource}] {desc}"
            if due:
                part += f" — 到期: {due}"
            lines.append(part)
        lines.append("")

    total = len(all_concerns)
    if total > 0:
        parts = [f"{k}={v}" for k, v in sorted(counts.items()) if v > 0]
        lines.append(f"**统计**: {', '.join(parts)}, total={total}")
    else:
        lines.append("**所有资源无 open 关注项**")

    return "\n".join(lines)
