"""CLI 端到端测试 (typer.testing.CliRunner).

覆盖 spec I/O 矩阵: JSON/人类格式输出、退出码、secret 脱敏、环路错误、
必填字段错误、--force 删除、--within 到期查询.
每个测试用独立的临时 DB (通过 OPSCTL_DB 环境变量).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opsctl import cli as cli_module
from opsctl.cli import app


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    db = tmp_path / "test.db"
    monkeypatch.setenv("OPSCTL_DB", str(db))
    # CliRunner 每条命令独立进程级状态, 重置全局 JSON 标志
    cli_module._JSON_FLAG["value"] = False
    return CliRunner()


def _add_ecs(runner: CliRunner, name="web1", extra=None):
    args = [
        "resource",
        "add",
        "--json",
        "--type",
        "ecs",
        "--name",
        name,
        "--attr",
        "host=10.0.0.1",
        "--attr",
        "ssh_user=root",
    ]
    if extra:
        args += extra
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


def test_resource_add_outputs_json(runner):
    out = _add_ecs(runner, name="w1")
    assert out["name"] == "w1"
    assert out["type"] == "ecs"


def test_resource_add_rejects_unregistered_type_json(runner):
    res = runner.invoke(app, ["resource", "add", "--json", "--type", "ghost", "--name", "x"])
    assert res.exit_code == 1
    assert "error" in json.loads(res.output)


def test_resource_add_rejects_missing_required(runner):
    res = runner.invoke(app, ["resource", "add", "--json", "--type", "ecs", "--name", "x"])
    assert res.exit_code == 1
    assert "必填" in json.loads(res.output)["error"]


def test_resource_list_json(runner):
    _add_ecs(runner, name="w1")
    _add_ecs(runner, name="w2")
    res = runner.invoke(app, ["resource", "list", "--json"])
    assert res.exit_code == 0
    names = {r["name"] for r in json.loads(res.output)}
    assert names == {"w1", "w2"}


def test_resource_list_filter_by_type(runner):
    _add_ecs(runner, name="w1")
    res = runner.invoke(app, ["resource", "list", "--json", "--type", "redis"])
    # redis 是合法类型但无数据 -> 空数组
    assert res.exit_code == 0
    assert json.loads(res.output) == []


def test_resource_show_masks_secret_by_default(runner):
    _add_ecs(runner, name="w1", extra=["--attr", "ssh_password:secret=hunter2"])
    res = runner.invoke(app, ["resource", "show", "--json", "w1"])
    out = json.loads(res.output)
    pw = next(a for a in out["attributes"] if a["key"] == "ssh_password")
    assert pw["value"] == "***"


def test_resource_show_reveal_shows_plaintext(runner):
    _add_ecs(runner, name="w1", extra=["--attr", "ssh_password:secret=hunter2"])
    res = runner.invoke(app, ["resource", "show", "--json", "--reveal", "w1"])
    out = json.loads(res.output)
    pw = next(a for a in out["attributes"] if a["key"] == "ssh_password")
    assert pw["value"] == "hunter2"


def test_resource_update_adds_extension_attr(runner):
    _add_ecs(runner, name="w1")
    res = runner.invoke(app, ["resource", "update", "--json", "w1", "--attr", "owner=team-a"])
    out = json.loads(res.output)
    owner = next(a for a in out["attributes"] if a["key"] == "owner")
    assert owner["value"] == "team-a" and owner["is_standard"] == 0


def test_relation_add_rejects_cycle_json(runner):
    _add_ecs(runner, name="a")
    _add_ecs(runner, name="b")
    r1 = runner.invoke(app, ["relation", "add", "--json", "--source", "a", "--target", "b"])
    assert r1.exit_code == 0
    r2 = runner.invoke(app, ["relation", "add", "--json", "--source", "b", "--target", "a"])
    assert r2.exit_code == 1
    assert "循环" in json.loads(r2.output)["error"]


def test_resource_delete_blocks_when_referenced(runner):
    _add_ecs(runner, name="web")
    _add_ecs(runner, name="db")
    runner.invoke(app, ["relation", "add", "--json", "--source", "web", "--target", "db"])
    res = runner.invoke(app, ["resource", "delete", "--json", "db"])
    assert res.exit_code == 1
    assert "引用" in json.loads(res.output)["error"]


def test_resource_delete_force_succeeds(runner):
    _add_ecs(runner, name="web")
    _add_ecs(runner, name="db")
    runner.invoke(app, ["relation", "add", "--json", "--source", "web", "--target", "db"])
    res = runner.invoke(app, ["resource", "delete", "--json", "--force", "db"])
    assert res.exit_code == 0


def test_concern_due_filters_by_window(runner):
    from datetime import UTC, datetime, timedelta

    _add_ecs(runner, name="w1")
    now = datetime.now(UTC)
    soon = (now + timedelta(days=5)).date().isoformat()
    later = (now + timedelta(days=20)).date().isoformat()
    runner.invoke(
        app,
        [
            "concern",
            "add",
            "--json",
            "--resource",
            "w1",
            "--category",
            "expiry",
            "--desc",
            "SSL",
            "--due",
            soon,
            "--severity",
            "critical",
        ],
    )
    runner.invoke(
        app,
        [
            "concern",
            "add",
            "--json",
            "--resource",
            "w1",
            "--category",
            "renewal",
            "--desc",
            "续费",
            "--due",
            later,
        ],
    )
    res = runner.invoke(app, ["concern", "due", "--json", "--within", "7d"])
    assert res.exit_code == 0
    items = json.loads(res.output)
    assert len(items) == 1
    assert items[0]["desc"] == "SSL"


def test_concern_add_rejects_bad_due(runner):
    _add_ecs(runner, name="w1")
    res = runner.invoke(
        app,
        [
            "concern",
            "add",
            "--json",
            "--resource",
            "w1",
            "--category",
            "x",
            "--desc",
            "y",
            "--due",
            "bad",
        ],
    )
    assert res.exit_code == 1


def test_resource_types_lists_all(runner):
    res = runner.invoke(app, ["resource", "types", "--json"])
    assert res.exit_code == 0
    types = json.loads(res.output)
    assert {"ecs", "redis", "postgres", "mysql", "hbase", "aliyun_account", "service"} <= set(types)


def test_human_format_output_no_json_flag(runner):
    _add_ecs(runner, name="w1")
    # 不带 --json 的人类格式
    res = runner.invoke(app, ["resource", "list"])
    assert res.exit_code == 0
    assert "w1" in res.output


def test_unknown_resource_returns_error(runner):
    res = runner.invoke(app, ["resource", "show", "--json", "ghost"])
    assert res.exit_code == 1
    assert "error" in json.loads(res.output)


def test_resource_update_rejects_clearing_required_field(runner):
    _add_ecs(runner, name="w1")
    res = runner.invoke(app, ["resource", "update", "--json", "w1", "--attr", "host="])
    assert res.exit_code == 1
    assert "必填" in json.loads(res.output)["error"]


def test_parse_attr_rejects_empty_key(runner):
    res = runner.invoke(
        app, ["resource", "add", "--json", "--type", "ecs", "--name", "bad", "--attr", "=value"]
    )
    assert res.exit_code == 2


def test_parse_attr_rejects_invalid_vtype(runner):
    res = runner.invoke(
        app, ["resource", "add", "--json", "--type", "ecs", "--name", "bad", "--attr", "x:bogus=1"]
    )
    assert res.exit_code == 2


def test_parse_attr_strips_value_whitespace(runner):
    # value 前后空白应被 strip 再入库
    res = runner.invoke(
        app,
        [
            "resource",
            "add",
            "--json",
            "--type",
            "ecs",
            "--name",
            "spaced",
            "--attr",
            " host = 1.2.3.4 ",
            "--attr",
            "ssh_user = root ",
        ],
    )
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    host = next(a for a in out["attributes"] if a["key"] == "host")
    assert host["value"] == "1.2.3.4"
    user = next(a for a in out["attributes"] if a["key"] == "ssh_user")
    assert user["value"] == "root"


# ---------- relation CLI ----------


def test_relation_list_json(runner):
    _add_ecs(runner, name="web")
    _add_ecs(runner, name="db")
    runner.invoke(app, ["relation", "add", "--json", "--source", "web", "--target", "db"])
    res = runner.invoke(app, ["relation", "list", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert len(data) == 1
    assert data[0]["source"] != "" and data[0]["target"] != ""


def test_relation_list_empty(runner):
    # 空库应返回空 JSON 数组
    res = runner.invoke(app, ["relation", "list", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output) == []


def test_relation_list_filter_by_resource(runner):
    _add_ecs(runner, name="web")
    _add_ecs(runner, name="db")
    _add_ecs(runner, name="cache")
    runner.invoke(app, ["relation", "add", "--json", "--source", "web", "--target", "db"])
    res = runner.invoke(app, ["relation", "list", "--json", "--resource", "web"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert len(data) >= 1


def test_relation_graph_json(runner):
    _add_ecs(runner, name="app")
    _add_ecs(runner, name="pg")
    runner.invoke(app, ["relation", "add", "--json", "--source", "app", "--target", "pg"])
    res = runner.invoke(app, ["relation", "graph", "--json", "app"])
    assert res.exit_code == 0
    tree = json.loads(res.output)
    assert tree["resource"]["name"] == "app"
    assert len(tree["downstream"]) == 1


def test_relation_graph_not_found(runner):
    res = runner.invoke(app, ["relation", "graph", "--json", "ghost"])
    assert res.exit_code == 1
    assert "error" in json.loads(res.output)


def test_relation_graph_human_format(runner):
    _add_ecs(runner, name="app")
    _add_ecs(runner, name="pg")
    runner.invoke(app, ["relation", "add", "--json", "--source", "app", "--target", "pg"])
    res = runner.invoke(app, ["relation", "graph", "app"])
    assert res.exit_code == 0
    assert "依赖" in res.output
    assert "pg" in res.output


def test_relation_add_duplicate_is_idempotent(runner):
    _add_ecs(runner, name="a")
    _add_ecs(runner, name="b")
    r1 = runner.invoke(app, ["relation", "add", "--json", "--source", "a", "--target", "b"])
    assert r1.exit_code == 0
    r2 = runner.invoke(app, ["relation", "add", "--json", "--source", "a", "--target", "b"])
    assert r2.exit_code == 0
    # 幂等: 两次 add 都应成功, 不会报错
    rl = runner.invoke(app, ["relation", "list", "--json"])
    assert len(json.loads(rl.output)) == 1


# ---------- concern CLI ----------


def test_concern_list_json(runner):
    _add_ecs(runner, name="w1")
    runner.invoke(
        app,
        ["concern", "add", "--json", "--resource", "w1", "--category", "expiry", "--desc", "SSL"],
    )
    res = runner.invoke(app, ["concern", "list", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert len(data) == 1
    assert data[0]["desc"] == "SSL"


def test_concern_list_filter_by_resource(runner):
    _add_ecs(runner, name="w1")
    _add_ecs(runner, name="w2")
    runner.invoke(
        app,
        ["concern", "add", "--json", "--resource", "w1", "--category", "x", "--desc", "w1-only"],
    )
    res = runner.invoke(app, ["concern", "list", "--json", "--resource", "w1"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert len(data) == 1
    assert data[0]["desc"] == "w1-only"


def test_concern_list_empty(runner):
    res = runner.invoke(app, ["concern", "list", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output) == []


def test_concern_due_empty(runner):
    res = runner.invoke(app, ["concern", "due", "--json", "--within", "7d"])
    assert res.exit_code == 0
    assert json.loads(res.output) == []


def test_concern_due_rejects_bad_within(runner):
    res = runner.invoke(app, ["concern", "due", "--json", "--within", "7x"])
    assert res.exit_code == 1


def test_concern_due_rejects_negative_within(runner):
    res = runner.invoke(app, ["concern", "due", "--json", "--within", "-3d"])
    assert res.exit_code == 1


def test_add_concern_with_all_options(runner):
    _add_ecs(runner, name="w1")
    res = runner.invoke(
        app,
        [
            "concern",
            "add",
            "--json",
            "--resource",
            "w1",
            "--category",
            "capacity",
            "--desc",
            "磁盘80%",
            "--due",
            "2026-12-31T00:00:00+00:00",
            "--severity",
            "warning",
        ],
    )
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["category"] == "capacity"
    assert out["severity"] == "warning"


def test_add_concern_bad_severity(runner):
    _add_ecs(runner, name="w1")
    res = runner.invoke(
        app,
        [
            "concern",
            "add",
            "--json",
            "--resource",
            "w1",
            "--category",
            "x",
            "--desc",
            "y",
            "--severity",
            "fatal",
        ],
    )
    assert res.exit_code == 1
    assert "error" in json.loads(res.output)
