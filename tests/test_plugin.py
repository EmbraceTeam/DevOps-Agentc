"""Plugin 工具 handler 测试 (mock subprocess).

验证: schema 数量与命名、cli_args 转换、handler 解析 CLI JSON、错误包装.
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

from opsctl.plugin import _handle_inspect, register
from opsctl.plugin.schemas import PLUGIN_TOOLS
from opsctl.plugin.tools import _opsctl_binary, _run_opsctl, make_handler

# 真实 UUID 形态的 resource_id, 确保测试断言渲染 name 而非 UUID
_UUID_A = "3f8a2b1c-1a2b-3c4d-5e6f-7a8b9c0d1e2f"
_UUID_B = "4f9c3d2e-2b3c-4d5e-6f70-8b9c0d1e2f30"
_UUID_C = "5a0d4e3f-3c4d-5e6f-7081-9c0d1e2f3041"
_UUID_D = "6b1e5f40-4d5e-6f70-8192-0d1e2f304152"


def _fake_items() -> list[dict]:
    """跨 urgency × severity 各档的到期项 (含 later+critical)."""
    return [
        {
            "id": 1,
            "resource": _UUID_A,
            "name": "pg-main",
            "category": "capacity",
            "desc": "磁盘水位 92%",
            "due": "2026-08-07T20:00:00+08:00",
            "severity": "critical",
            "urgency": "urgent",
        },
        {
            "id": 2,
            "resource": _UUID_B,
            "name": "web-prod-1",
            "category": "expiry",
            "desc": "SSL 证书到期",
            "due": "2026-08-07T15:00:00+08:00",
            "severity": "warning",
            "urgency": "urgent",
        },
        {
            "id": 3,
            "resource": _UUID_C,
            "name": "redis-cache",
            "category": "capacity",
            "desc": "内存水位 85%",
            "due": "2026-08-10",
            "severity": "warning",
            "urgency": "soon",
        },
        {
            "id": 4,
            "resource": _UUID_D,
            "name": "api-gw",
            "category": "expiry",
            "desc": "证书 2027 到期",
            "due": "2026-08-27",
            "severity": "info",
            "urgency": "later",
        },
        {
            "id": 5,
            "resource": _UUID_D,
            "name": "etcd-1",
            "category": "renewal",
            "desc": "备份配额",
            "due": "2026-08-28",
            "severity": "info",
            "urgency": "later",
        },
    ]


class FakeCtx:
    """捕获 register_tool/register_skill/register_command 调用, 便于断言."""

    def __init__(self):
        self.registered = {}
        self.skills = {}
        self.commands = {}

    def register_tool(self, *, name, toolset, schema, handler, description="", emoji=""):
        self.registered[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            "description": description,
            "emoji": emoji,
        }

    def register_skill(self, *, name, path):
        self.skills[name] = path

    def register_command(self, *, name, description, handler):
        self.commands[name] = {"description": description, "handler": handler}


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
    # 已注册技能和 slash 命令
    assert "ops-inspect" in ctx.skills
    assert "ops-inspect" in ctx.commands


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


def test_relation_graph_cli_args_builds():
    fn = PLUGIN_TOOLS["ops_relation_graph"]["cli_args"]
    assert fn({"name": "pg"}) == ["relation", "graph", "--json", "pg"]


def test_list_concerns_cli_args_builds():
    fn = PLUGIN_TOOLS["ops_list_concerns"]["cli_args"]
    assert fn({}) == ["concern", "list", "--json"]
    assert fn({"resource": "w1", "status": "resolved"}) == [
        "concern",
        "list",
        "--json",
        "--resource",
        "w1",
        "--status",
        "resolved",
    ]


def test_concerns_due_cli_args_builds():
    fn = PLUGIN_TOOLS["ops_concerns_due"]["cli_args"]
    assert fn({"within": "7d"}) == ["concern", "due", "--json", "--within", "7d"]


def test_add_resource_cli_args_passes_attributes():
    fn = PLUGIN_TOOLS["ops_add_resource"]["cli_args"]
    args = fn({"type": "ecs", "name": "w1", "attributes": ["host=1.2.3.4", "ssh_user=root"]})
    assert "--attr" in args
    assert "host=1.2.3.4" in args


def test_add_resource_cli_args_passes_port_and_endpoint():
    fn = PLUGIN_TOOLS["ops_add_resource"]["cli_args"]
    args = fn({"type": "ecs", "name": "w1", "endpoint": "10.0.0.1", "port": 22})
    assert "--endpoint" in args and "10.0.0.1" in args
    assert "--port" in args and "22" in args


def test_update_resource_cli_args_builds():
    fn = PLUGIN_TOOLS["ops_update_resource"]["cli_args"]
    args = fn({"name": "w1", "status": "inactive", "attributes": ["owner=team-a"]})
    assert args[0] == "resource" and args[1] == "update"
    assert "w1" in args
    assert "--status" in args and "inactive" in args
    assert "--attr" in args and "owner=team-a" in args


def test_update_resource_cli_args_clears_endpoint():
    fn = PLUGIN_TOOLS["ops_update_resource"]["cli_args"]
    args = fn({"name": "w1", "endpoint": ""})
    assert "--endpoint" in args


def test_delete_resource_cli_args_default_no_force():
    fn = PLUGIN_TOOLS["ops_delete_resource"]["cli_args"]
    args = fn({"name": "w1"})
    assert "--force" not in args
    assert "w1" in args


def test_delete_resource_cli_args_force_when_requested():
    fn = PLUGIN_TOOLS["ops_delete_resource"]["cli_args"]
    args = fn({"name": "w1", "force": True})
    assert "--force" in args


def test_delete_relation_cli_args_builds():
    fn = PLUGIN_TOOLS["ops_delete_relation"]["cli_args"]
    args = fn({"source": "a", "target": "b"})
    assert "--source" in args and "a" in args
    assert "--target" in args and "b" in args


def test_add_concern_cli_args_builds():
    fn = PLUGIN_TOOLS["ops_add_concern"]["cli_args"]
    args = fn(
        {
            "resource": "w1",
            "category": "expiry",
            "description": "test",
            "due": "2026-12-31",
            "severity": "critical",
        }
    )
    assert "--resource" in args and "w1" in args
    assert "--due" in args and "2026-12-31" in args
    assert "--severity" in args and "critical" in args


def test_resolve_concern_cli_args_builds():
    fn = PLUGIN_TOOLS["ops_resolve_concern"]["cli_args"]
    args = fn({"id": 42})
    assert "42" in args


# ---------- /ops-inspect _handle_inspect ----------


def test_handle_inspect_three_groups_with_names():
    """三组输出, 渲染 name 而非 UUID, 头部统计含 other 桶."""
    items = _fake_items()
    with patch("opsctl.plugin.tools._run_opsctl", return_value=items) as m:
        out = _handle_inspect("--within 30d")
    # 三组标题
    assert "🔴 需立即处理" in out
    assert "🟡 需关注" in out
    assert "🔵 其余 2 项" in out  # 2 个 later 项折叠
    # 渲染 name 而非 UUID: 全量组的条目逐条显示 name; 折叠组不逐条渲染
    for it in items[:3]:
        assert it["name"] in out
    for it in items:
        assert it["resource"] not in out
    # 折叠组只有一行统计, 不逐条展开
    assert "api-gw" not in out
    assert "etcd-1" not in out
    # 头部统计: critical=1, warning=2, info=2, other=0, 共 5
    assert "critical=1" in out
    assert "warning=2" in out
    assert "info=2" in out
    assert "other=0" in out
    assert "共 5" in out
    m.assert_called_once_with(["concern", "due", "--json", "--within", "30d"])


def test_handle_inspect_zero_due_items():
    with patch("opsctl.plugin.tools._run_opsctl", return_value=[]):
        out = _handle_inspect("")
    assert "无到期关注项" in out
    assert "所有资源无 open 关注项" not in out


def test_handle_inspect_window_space_form():
    with patch("opsctl.plugin.tools._run_opsctl", return_value=_fake_items()) as m:
        _handle_inspect("--within 7d")
    assert m.call_args.args[0] == ["concern", "due", "--json", "--within", "7d"]


def test_handle_inspect_window_equals_form():
    with patch("opsctl.plugin.tools._run_opsctl", return_value=_fake_items()) as m:
        _handle_inspect("--within=7d")
    assert m.call_args.args[0] == ["concern", "due", "--json", "--within", "7d"]


def test_handle_inspect_default_window_is_30d():
    with patch("opsctl.plugin.tools._run_opsctl", return_value=[]) as m:
        _handle_inspect("")
    assert m.call_args.args[0] == ["concern", "due", "--json", "--within", "30d"]


def test_handle_inspect_missing_within_value_reports_error():
    with patch("opsctl.plugin.tools._run_opsctl") as m:
        out = _handle_inspect("--within")
    assert "巡检失败" in out
    m.assert_not_called()


def test_handle_inspect_invalid_window_goes_cli_error_path():
    # 非法值由 CLI 返回 error dict -> 巡检失败消息
    with patch(
        "opsctl.plugin.tools._run_opsctl",
        return_value={"error": "非法 --within 单位 'x', 支持 d/h/m (大小写不敏感)"},
    ) as m:
        out = _handle_inspect("--within 7x")
    assert "巡检失败" in out
    m.assert_called_once_with(["concern", "due", "--json", "--within", "7x"])


def test_handle_inspect_shlex_unterminated_quote_defensive():
    with patch("opsctl.plugin.tools._run_opsctl") as m:
        out = _handle_inspect('--within "7d')
    assert "巡检失败" in out
    m.assert_not_called()


def test_handle_inspect_non_str_args_defensive():
    with patch("opsctl.plugin.tools._run_opsctl") as m:
        out = _handle_inspect(None)  # type: ignore[arg-type]
    assert "巡检失败" in out
    m.assert_not_called()


def test_handle_inspect_rejects_non_dict_items():
    with patch("opsctl.plugin.tools._run_opsctl", return_value=[{"id": 1}, "oops"]):
        out = _handle_inspect("")
    assert "巡检失败: 返回项格式异常" in out


def test_handle_inspect_empty_quoted_within_reports_error():
    # --within "" (空值) 不得静默回退默认窗口
    with patch("opsctl.plugin.tools._run_opsctl") as m:
        out = _handle_inspect('--within ""')
    assert "巡检失败" in out
    m.assert_not_called()


def test_handle_inspect_within_value_swallowed_by_flag_reports_error():
    # --within 后跟 flag (--within --json) 报缺值, 不把 flag 当窗口值
    with patch("opsctl.plugin.tools._run_opsctl") as m:
        out = _handle_inspect("--within --json")
    assert "巡检失败" in out
    m.assert_not_called()


def test_handle_inspect_duplicate_within_reports_error():
    with patch("opsctl.plugin.tools._run_opsctl") as m:
        out = _handle_inspect("--within 7d --within 30d")
    assert "巡检失败" in out
    m.assert_not_called()


def test_handle_inspect_unknown_flag_reports_error():
    # 近似拼写 (--Within=7d / --withinx) 不得静默忽略回退默认窗口
    with patch("opsctl.plugin.tools._run_opsctl") as m:
        out = _handle_inspect("--Within=7d")
    assert "巡检失败" in out
    m.assert_not_called()


def test_handle_inspect_bare_value_reports_error():
    # 裸值 (7d) 前无 flag, 报错而非静默忽略
    with patch("opsctl.plugin.tools._run_opsctl") as m:
        out = _handle_inspect("7d")
    assert "巡检失败" in out
    m.assert_not_called()


def test_handle_inspect_rejects_non_list_result():
    with patch("opsctl.plugin.tools._run_opsctl", return_value={"error": "opsctl 无输出"}):
        out = _handle_inspect("")
    assert "巡检失败" in out


def test_handle_inspect_unknown_severity_goes_to_other_bucket():
    items = [
        {
            "id": 1,
            "resource": _UUID_A,
            "name": "svc-a",
            "desc": "未知 severity 但 urgent",
            "due": "2026-08-07T10:00:00+00:00",
            "severity": "fatal",
            "urgency": "urgent",
        }
    ]
    with patch("opsctl.plugin.tools._run_opsctl", return_value=items):
        out = _handle_inspect("")
    # fatal 计入 other 桶, 共 N = critical+warning+info+other
    assert "other=1" in out
    assert "共 1" in out
    # urgent 规则 -> 🔴 组, 渲染 name
    assert "🔴 需立即处理" in out
    assert "svc-a" in out
    assert _UUID_A not in out


def test_handle_inspect_missing_urgency_key_defensive():
    items = [
        {
            "id": 1,
            "resource": _UUID_A,
            "name": "svc-a",
            "desc": "缺 urgency 且 info",
            "due": "2026-08-27",
            "severity": "info",
        },
        {
            "id": 2,
            "resource": _UUID_B,
            "name": "svc-b",
            "desc": "缺 urgency 但 critical",
            "due": "2026-08-27",
            "severity": "critical",
        },
    ]
    with patch("opsctl.plugin.tools._run_opsctl", return_value=items):
        out = _handle_inspect("")
    # 缺 urgency 按 later 归组: svc-a -> 🔵, svc-b (critical) -> 🔴
    assert "🔴 需立即处理" in out
    assert "svc-b" in out
    assert "🔵 其余 1 项" in out
    assert "共 2" in out


# ---- 目录插件 shim 定位与调用 (方案 A′: 插件自包含 CLI) ----


def test_opsctl_binary_prefers_repo_shim():
    """仓库内 bin/opsctl_shim.py 存在时优先返回 (CLI 随插件 git 更新)."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("opsctl.plugin.tools.shutil.which", return_value="/usr/bin/opsctl"):
            binary = _opsctl_binary()
    assert binary.endswith("bin/opsctl_shim.py")


def test_opsctl_binary_falls_back_to_path_when_no_shim():
    """无仓库 shim 时退回 PATH 中的 opsctl (pip 独立安装)."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("opsctl.plugin.tools.shutil.which", return_value="/usr/bin/opsctl"):
            with patch("pathlib.Path.is_file", return_value=False):
                assert _opsctl_binary() == "/usr/bin/opsctl"


def test_opsctl_binary_env_override_wins():
    """OPSCTL_BINARY 环境变量覆盖一切定位逻辑."""
    with patch.dict(os.environ, {"OPSCTL_BINARY": "/custom/opsctl"}, clear=True):
        assert _opsctl_binary() == "/custom/opsctl"


def test_run_opsctl_uses_sys_executable_for_py_shim():
    """.py shim 用当前进程 Python 执行 (Hermes venv)."""
    with patch("opsctl.plugin.tools._opsctl_binary", return_value="/tmp/opsctl_shim.py"):
        with patch("opsctl.plugin.tools.subprocess.run") as m:
            m.return_value = SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")
            result = _run_opsctl(["resource", "list", "--json"])
    assert result == {"ok": True}
    cmd = m.call_args.args[0]
    assert cmd[0] == sys.executable
    assert cmd[1] == "/tmp/opsctl_shim.py"
    assert cmd[2:] == ["resource", "list", "--json"]


def test_run_opsctl_uses_binary_directly_for_exe():
    """非 .py 可执行文件直接调用 (不经过 sys.executable)."""
    with patch("opsctl.plugin.tools._opsctl_binary", return_value="/usr/bin/opsctl"):
        with patch("opsctl.plugin.tools.subprocess.run") as m:
            m.return_value = SimpleNamespace(returncode=0, stdout="[]", stderr="")
            result = _run_opsctl(["resource", "list", "--json"])
    assert result == []
    cmd = m.call_args.args[0]
    assert cmd[0] == "/usr/bin/opsctl"
    assert cmd[1:] == ["resource", "list", "--json"]
