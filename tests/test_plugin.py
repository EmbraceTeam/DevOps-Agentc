"""Plugin 工具 handler 测试 (mock subprocess).

验证: schema 数量与命名、cli_args 转换、handler 解析 CLI JSON、错误包装.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from opsctl.plugin import register
from opsctl.plugin.schemas import PLUGIN_TOOLS
from opsctl.plugin.tools import _run_opsctl, make_handler


class FakeCtx:
    """捕获 register_tool 调用, 便于断言."""

    def __init__(self):
        self.registered = {}

    def register_tool(self, *, name, toolset, schema, handler, description="", emoji=""):
        self.registered[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            "description": description,
            "emoji": emoji,
        }


def test_register_registers_all_tools():
    ctx = FakeCtx()
    register(ctx)
    assert set(ctx.registered.keys()) == set(PLUGIN_TOOLS.keys())
    # 全部归入 opsctl toolset
    assert all(v["toolset"] == "opsctl" for v in ctx.registered.values())
    # 每个 schema 有 name 与 description
    for name, entry in ctx.registered.items():
        assert entry["schema"]["name"] == name
        assert entry["schema"]["description"]


def test_make_handler_parses_cli_json():
    fake_output = [{"name": "web1", "type": "ecs"}]
    with patch("opsctl.plugin.tools._run_opsctl", return_value=fake_output):
        handler = make_handler(lambda p: ["resource", "list", "--json"])
        result = json.loads(handler({}))
    assert result == fake_output


def test_make_handler_wraps_error():
    with patch("opsctl.plugin.tools._run_opsctl", return_value={"error": "boom"}):
        handler = make_handler(lambda p: ["resource", "show", "--json", "ghost"])
        result = json.loads(handler({"name": "ghost"}))
    assert result == {"error": "boom"}


def test_run_opsctl_returns_error_on_missing_binary():
    # 不存在的二进制 -> {"error": ...}
    import os

    with patch.dict(os.environ, {"OPSCTL_BINARY": "/nonexistent/opsctl-xyz"}):
        result = _run_opsctl(["resource", "list", "--json"])
    assert "error" in result


def test_run_opsctl_returns_error_on_nonzero_exit():
    # 用 python -c 模拟一个立即非零退出的"opsctl"
    import os

    fake = "python3"
    with patch.dict(os.environ, {"OPSCTL_BINARY": fake}):
        # sys.exit(1) 触发非零退出
        result = _run_opsctl(["-c", "import sys; sys.exit(1)"])
    assert "error" in result or "returncode" in result


def test_list_resources_cli_args_build():
    fn = PLUGIN_TOOLS["ops_list_resources"]["cli_args"]
    assert fn({}) == ["resource", "list", "--json"]
    assert fn({"type": "ecs"}) == ["resource", "list", "--json", "--type", "ecs"]


def test_show_resource_cli_args_default_no_reveal():
    fn = PLUGIN_TOOLS["ops_show_resource"]["cli_args"]
    args = fn({"name": "web1"})
    assert "--reveal" not in args  # 默认不暴露凭据
    assert "web1" in args


def test_show_resource_cli_args_reveal_when_requested():
    fn = PLUGIN_TOOLS["ops_show_resource"]["cli_args"]
    args = fn({"name": "web1", "reveal": True})
    assert "--reveal" in args
    assert "web1" in args


def test_add_relation_cli_args_builds():
    fn = PLUGIN_TOOLS["ops_add_relation"]["cli_args"]
    args = fn({"source": "a", "target": "b"})
    assert "--source" in args and "a" in args
    assert "--target" in args and "b" in args
    # 自定义 type 也应透传
    args2 = fn({"source": "a", "target": "b", "type": "connects_to"})
    assert "connects_to" in args2


def test_relation_graph_cli_args_builds():
    fn = PLUGIN_TOOLS["ops_relation_graph"]["cli_args"]
    assert fn({"name": "pg"}) == ["relation", "graph", "--json", "pg"]


def test_list_concerns_cli_args_builds():
    fn = PLUGIN_TOOLS["ops_list_concerns"]["cli_args"]
    assert fn({}) == ["concern", "list", "--json"]
    assert fn({"resource": "w1", "status": "resolved"}) == [
        "concern", "list", "--json", "--resource", "w1", "--status", "resolved",
    ]


def test_concerns_due_cli_args_builds():
    fn = PLUGIN_TOOLS["ops_concerns_due"]["cli_args"]
    assert fn({"within": "7d"}) == ["concern", "due", "--json", "--within", "7d"]


def test_add_resource_cli_args_passes_attributes():
    fn = PLUGIN_TOOLS["ops_add_resource"]["cli_args"]
    args = fn({"type": "ecs", "name": "w1", "attributes": ["host=1.2.3.4", "ssh_user=root"]})
    assert "--attr" in args
    assert "host=1.2.3.4" in args
