"""Hermes Plugin 注册入口.

被 ``hermes_agent.plugins`` entry point 加载. Plugin 把 opsctl CLI 子命令通过
``ctx.register_tool()`` 暴露给 LLM. handler 永远通过 subprocess 调 ``opsctl --json``,
不直接 import repository (见 spec Design Notes: Plugin ↔ CLI 解耦).
"""

from __future__ import annotations

from .schemas import PLUGIN_TOOLS
from .tools import make_handler


def register(ctx) -> None:  # type: ignore[no-untyped-def]
    """Hermes Plugin 注册钩子. ctx 由 Hermes 注入, 提供 register_tool 等 API."""
    for tool_name, spec in PLUGIN_TOOLS.items():
        ctx.register_tool(
            name=tool_name,
            toolset="opsctl",
            schema=spec["schema"],
            handler=make_handler(spec["cli_args"]),
            description=spec["schema"]["description"],
            emoji="🛠️",
        )
