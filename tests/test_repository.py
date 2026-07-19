"""数据层测试: CRUD + 环路检测 + 级联检查 + 到期查询.

用 in-memory SQLite + 直接调用 repository 函数, 覆盖 spec I/O 矩阵边界.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from opsctl import builtin_types  # noqa: F401 — 触发类型注册
from opsctl import repository as repo
from opsctl.db import get_db
from opsctl.models import CycleError, ResourceTypeNotFoundError


@pytest.fixture
def conn() -> sqlite3.Connection:
    """每个测试用独立的 in-memory DB."""
    c = get_db(":memory:")
    yield c
    c.close()


def _add_ecs(conn, name="web1", **attrs):
    base = {"host": ("10.0.0.1", "str"), "ssh_user": ("root", "str")}
    base.update({k: (v, "str") for k, v in attrs.items()})
    return repo.create_resource(conn, type_name="ecs", name=name, attributes=base)


# ---------- 资源 CRUD ----------


def test_create_resource_writes_main_and_attributes(conn):
    res = _add_ecs(conn)
    assert res["name"] == "web1"
    assert res["type"] == "ecs"
    keys = {a["key"] for a in res["attributes"]}
    assert {"host", "ssh_user"} <= keys
    # host 是 ecs 标准字段 -> is_standard=1
    host_attr = next(a for a in res["attributes"] if a["key"] == "host")
    assert host_attr["is_standard"] == 1


def test_create_resource_rejects_unregistered_type(conn):
    with pytest.raises(ResourceTypeNotFoundError):
        repo.create_resource(conn, type_name="nope", name="x", attributes={})


def test_create_resource_rejects_missing_required(conn):
    with pytest.raises(ValueError, match="必填"):
        repo.create_resource(conn, type_name="ecs", name="bad", attributes={})


def test_create_resource_applies_default_for_standard_field(conn):
    # ssh_port 有 default=22, 未传也应自动填入
    res = _add_ecs(conn)
    ssh_port = next(a for a in res["attributes"] if a["key"] == "ssh_port")
    assert ssh_port["value"] == "22"


def test_list_resources_filters_by_type(conn):
    _add_ecs(conn, "web1")
    repo.create_resource(
        conn,
        type_name="redis",
        name="r1",
        attributes={"host": ("r", "str")},
    )
    ecs_only = repo.list_resources(conn, type_filter="ecs")
    assert len(ecs_only) == 1 and ecs_only[0]["name"] == "web1"
    all_items = repo.list_resources(conn)
    assert len(all_items) == 2


def test_list_resources_rejects_unregistered_filter(conn):
    with pytest.raises(ResourceTypeNotFoundError):
        repo.list_resources(conn, type_filter="ghost")


def test_update_resource_adds_extension_attribute(conn):
    _add_ecs(conn, "web1")
    updated = repo.update_resource(conn, "web1", attributes={"owner": ("team-a", "str")})
    owner = next(a for a in updated["attributes"] if a["key"] == "owner")
    assert owner["value"] == "team-a"
    assert owner["is_standard"] == 0  # 扩展属性


def test_delete_resource_blocks_when_referenced(conn):
    _add_ecs(conn, "web1")
    repo.create_resource(
        conn,
        type_name="postgres",
        name="pg",
        attributes={"host": ("p", "str"), "db_name": ("d", "str"), "username": ("u", "str")},
    )
    repo.add_relation(conn, source="web1", target="pg")
    with pytest.raises(ValueError, match="被.*引用"):
        repo.delete_resource(conn, "pg")


def test_delete_resource_force_cascades(conn):
    _add_ecs(conn, "web1")
    repo.create_resource(
        conn,
        type_name="postgres",
        name="pg",
        attributes={"host": ("p", "str"), "db_name": ("d", "str"), "username": ("u", "str")},
    )
    repo.add_relation(conn, source="web1", target="pg")
    repo.delete_resource(conn, "pg", force=True)
    with pytest.raises(KeyError):
        repo.get_resource(conn, "pg")


# ---------- 关系 + 环路检测 ----------


def test_add_relation_detects_cycle(conn):
    _add_ecs(conn, "a")
    _add_ecs(conn, "b")
    repo.add_relation(conn, source="a", target="b")  # a -> b
    # 再加 b -> a 会成环
    with pytest.raises(CycleError):
        repo.add_relation(conn, source="b", target="a")


def test_add_relation_detects_longer_cycle(conn):
    _add_ecs(conn, "a")
    _add_ecs(conn, "b")
    _add_ecs(conn, "c")
    repo.add_relation(conn, source="a", target="b")
    repo.add_relation(conn, source="b", target="c")
    # c -> a 形成 a->b->c->a
    with pytest.raises(CycleError):
        repo.add_relation(conn, source="c", target="a")


def test_relation_graph_returns_upstream_and_downstream(conn):
    _add_ecs(conn, "app")
    repo.create_resource(
        conn,
        type_name="postgres",
        name="pg",
        attributes={"host": ("p", "str"), "db_name": ("d", "str"), "username": ("u", "str")},
    )
    repo.add_relation(conn, source="app", target="pg")
    tree = repo.relation_graph(conn, "app")
    assert tree["resource"]["name"] == "app"
    assert len(tree["downstream"]) == 1
    assert tree["downstream"][0]["resource"]["name"] == "pg"
    # pg 的 upstream 应包含 app
    pg_tree = repo.relation_graph(conn, "pg")
    assert len(pg_tree["upstream"]) == 1


# ---------- 关注点 + 到期查询 ----------


def test_concerns_due_returns_only_open_within_window(conn):
    _add_ecs(conn, "web1")
    now = datetime.now(UTC)
    soon = (now + timedelta(days=5)).isoformat(timespec="seconds")
    later = (now + timedelta(days=10)).isoformat(timespec="seconds")
    repo.add_concern(conn, resource="web1", category="expiry", description="SSL证书", due_at=soon)
    repo.add_concern(conn, resource="web1", category="renewal", description="续费", due_at=later)
    # resolved 的不应出现
    c3 = repo.add_concern(conn, resource="web1", category="expiry", description="过期", due_at=soon)

    conn.execute("UPDATE concerns SET status='resolved' WHERE id=?", (c3.id,))

    due = repo.concerns_due(conn, within="7d")
    assert len(due) == 1
    assert due[0].description == "SSL证书"


def test_add_concern_rejects_bad_due_at(conn):
    _add_ecs(conn, "web1")
    with pytest.raises(ValueError, match="非法 due_at"):
        repo.add_concern(conn, resource="web1", category="x", description="y", due_at="not-a-date")


def test_parse_due_within_rejects_bad_format():
    with pytest.raises(ValueError):
        repo._parse_due_within("7x")
    with pytest.raises(ValueError):
        repo._parse_due_within("")


def test_parse_due_within_rejects_negative_and_huge():
    with pytest.raises(ValueError):
        repo._parse_due_within("-3d")
    with pytest.raises(ValueError):
        repo._parse_due_within("999999d")


def test_parse_due_within_accepts_uppercase_unit():
    # 大小写不敏感
    assert repo._parse_due_within("7D") >= repo._parse_due_within("0d")


def test_stringify_int_raises_with_key():
    with pytest.raises(ValueError, match="属性 'port'"):
        repo._stringify("abc", "int", "port")


def test_stringify_json_validates_string():
    with pytest.raises(ValueError, match="json"):
        repo._stringify("not-json", "json", "cfg")
    # 合法 json 字符串原样返回
    assert repo._stringify('{"a":1}', "json", "cfg") == '{"a":1}'


def test_create_resource_ignores_user_vtype_for_standard_field(conn):
    # 用户试图把 host 标成 secret, 标准字段类型由 schema 强制
    res = _add_ecs(conn, "w1")
    res = repo.update_resource(conn, "w1", attributes={"host": ("10.0.0.1", "secret")})
    host = next(a for a in res["attributes"] if a["key"] == "host")
    assert host["value_type"] == "str"  # schema 决定, 非 secret


def test_update_resource_blocks_clearing_required_field(conn):
    _add_ecs(conn, "w1")
    with pytest.raises(ValueError, match="必填"):
        repo.update_resource(conn, "w1", attributes={"host": ("", "str")})


def test_add_concern_rejects_bad_severity(conn):
    _add_ecs(conn, "w1")
    with pytest.raises(ValueError, match="severity"):
        repo.add_concern(conn, resource="w1", category="x", description="y", severity="fatal")


def test_add_concern_normalizes_due_at_to_utc(conn):
    _add_ecs(conn, "w1")
    c = repo.add_concern(
        conn, resource="w1", category="x", description="y",
        due_at="2026-12-31T00:00:00+08:00",  # 北京时间
    )
    # 归一化为 UTC: 2026-12-30T16:00:00+00:00
    assert c.due_at == "2026-12-30T16:00:00+00:00"


def test_add_relation_idempotent_on_duplicate(conn):
    _add_ecs(conn, "a")
    _add_ecs(conn, "b")
    r1 = repo.add_relation(conn, source="a", target="b")
    r2 = repo.add_relation(conn, source="a", target="b")
    assert r1.id == r2.id  # 幂等, 不产生重复


def test_relation_graph_handles_cycle_gracefully(conn):
    # 直接 SQL 写入绕过 add_relation 制造环, walk 应被 visited 保护
    _add_ecs(conn, "a")
    _add_ecs(conn, "b")
    a = repo.get_resource(conn, "a")["id"]
    b = repo.get_resource(conn, "b")["id"]
    conn.execute(
        "INSERT INTO relations (source_id,target_id,relation_type,note,created_at) "
        "VALUES (?,?,?,?,?)",
        (a, b, "depends_on", "", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO relations (source_id,target_id,relation_type,note,created_at) "
        "VALUES (?,?,?,?,?)",
        (b, a, "depends_on", "", "2026-01-01T00:00:00+00:00"),
    )
    # 不应无限递归 / 不抛 RecursionError
    tree = repo.relation_graph(conn, "a")
    assert tree["resource"]["name"] == "a"
