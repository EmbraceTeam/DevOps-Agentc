"""opsctl CLI 入口 (Typer).

设计: 每条命令同时输出 JSON (供 Plugin/Agent 解析) 与人类可读格式.
``--json`` 可作为全局选项 (命令前) 或每条子命令的选项 (命令后); 二者皆生效.
secret 字段默认脱敏, ``--reveal`` 显示明文.
"""

from __future__ import annotations

import json as json_lib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import builtin_types  # noqa: F401 — 触发类型注册
from . import repository as repo
from .db import get_db
from .models import CycleError, ResourceTypeNotFoundError, list_resource_types

app = typer.Typer(no_args_is_help=True, help="运维资源元数据管理 CLI")
resource_app = typer.Typer(no_args_is_help=True, help="资源管理")
relation_app = typer.Typer(no_args_is_help=True, help="资源关系管理")
concern_app = typer.Typer(no_args_is_help=True, help="资源关注点管理")
app.add_typer(resource_app, name="resource")
app.add_typer(relation_app, name="relation")
app.add_typer(concern_app, name="concern")

console = Console()
err_console = Console(stderr=True)

# 全局开关, 由根 callback 或子命令 --json 设置
_JSON_FLAG = {"value": False}

# 每个子命令共享的 --json 选项类型
JsonOption = Annotated[bool, typer.Option("--json", "-j", help="输出 JSON (供 Plugin/Agent 解析)")]


@contextmanager
def _db() -> Iterator:
    """打开连接并保证关闭 (sqlite3.Connection 的 with 仅做 commit/rollback, 不关闭)."""
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def _emit_json(data: object) -> None:
    print(json_lib.dumps(data, ensure_ascii=False, default=str))


def _set_json(value: bool) -> None:
    """子命令开头调用, 让 --json 在子命令位置也能生效.

    采用 OR 合并: 根 callback 先执行设初始值, 子命令本地的 --json 若为 True
    则提升为 True, 但不会把根已设的 True 拉回 False. 这样 `opsctl --json resource list`
    与 `opsctl resource list --json` 两种写法都生效.
    """
    _JSON_FLAG["value"] = _JSON_FLAG["value"] or value


def _fail(message: str, exc: Exception | None = None) -> None:
    detail = f"{message}: {exc}" if exc else message
    if _JSON_FLAG["value"]:
        print(json_lib.dumps({"error": detail}, ensure_ascii=False))
    else:
        err_console.print(f"[red]错误:[/red] {detail}")
    raise typer.Exit(code=1)


def _parse_attr(value: str) -> tuple[str, str, str]:
    """解析 `key=value` 或 `key:type=value` -> (key, value_type|'', raw_value).

    value_type 为空表示交给 repository 按是否标准字段决定.
    校验: key 非空、vtype 合法、raw 去除首尾空白.
    """
    from .models import VALID_VALUE_TYPES

    if "=" not in value:
        raise typer.BadParameter(f"非法 --attr 格式 '{value}', 应为 key=value 或 key:type=value")
    key_part, raw = value.split("=", 1)
    if ":" in key_part:
        key, vtype = key_part.split(":", 1)
        key, vtype = key.strip(), vtype.strip()
    else:
        key, vtype = key_part.strip(), ""
    if not key:
        raise typer.BadParameter(f"非法 --attr '{value}': key 不能为空")
    if vtype and vtype not in VALID_VALUE_TYPES:
        raise typer.BadParameter(
            f"非法 --attr '{value}': value_type '{vtype}' 不在 {sorted(VALID_VALUE_TYPES)}"
        )
    return key, vtype, raw.strip()


@app.callback()
def main_callback(
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="全局 JSON 输出 (置于子命令前)")
    ] = False,
) -> None:
    """opsctl — 运维资源元数据管理."""
    # 每次调用重置 (而非累加), 避免同进程多次调用间的状态污染
    _JSON_FLAG["value"] = json_output


def _mask(value: str, value_type: str, reveal: bool) -> str:
    if value_type == "secret" and not reveal:
        return "***"
    return value


# ---------- resource ----------


@resource_app.command("add")
def resource_add(
    type_name: Annotated[str, typer.Option("--type", help="资源类型 (见 types 命令)")],
    name: Annotated[str, typer.Option("--name", help="资源名 (唯一)")],
    json_output: JsonOption = False,
    endpoint: Annotated[str | None, typer.Option("--endpoint")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    attr: Annotated[list[str] | None, typer.Option("--attr", help="key[:type]=value")] = None,
) -> None:
    """新增资源."""
    _set_json(json_output)
    attributes = {}
    for a in attr or []:
        k, vt, raw = _parse_attr(a)
        attributes[k] = (raw, vt)
    try:
        with _db() as conn:
            created = repo.create_resource(
                conn,
                type_name=type_name,
                name=name,
                endpoint=endpoint,
                port=port,
                description=description,
                attributes=attributes,
            )
    except ResourceTypeNotFoundError as e:
        _fail(str(e))
    except ValueError as e:
        _fail(str(e))
    if _JSON_FLAG["value"]:
        _emit_json(_masked_resource(created, reveal=False))
    else:
        console.print(
            f"[green]✓[/green] 创建资源 [bold]{created['name']}[/bold] ({created['type']})"
        )
        console.print(f"  id: {created['id']}")


@resource_app.command("list")
def resource_list(
    json_output: JsonOption = False,
    type_name: Annotated[str | None, typer.Option("--type", help="按类型筛选")] = None,
) -> None:
    """列出资源."""
    _set_json(json_output)
    try:
        with _db() as conn:
            items = repo.list_resources(conn, type_filter=type_name)
    except ResourceTypeNotFoundError as e:
        _fail(str(e))
    if _JSON_FLAG["value"]:
        _emit_json(items)
        return
    table = Table(title="资源列表")
    for col in ("id", "name", "type", "endpoint", "port", "status"):
        table.add_column(col)
    for it in items:
        table.add_row(
            str(it["id"])[:8],
            it["name"],
            it["type"],
            str(it["endpoint"] or ""),
            str(it["port"] or ""),
            str(it["status"] or ""),
        )
    console.print(table)


@resource_app.command("show")
def resource_show(
    name: Annotated[str, typer.Argument(help="资源 id 或 name")],
    json_output: JsonOption = False,
    reveal: Annotated[bool, typer.Option("--reveal", help="显示 secret 明文")] = False,
) -> None:
    """查看资源详情 (含属性)."""
    _set_json(json_output)
    try:
        with _db() as conn:
            res = repo.get_resource(conn, name)
    except KeyError as e:
        _fail(str(e))
    data = _masked_resource(res, reveal=reveal)
    if _JSON_FLAG["value"]:
        _emit_json(data)
        return
    console.print(f"[bold]{data['name']}[/bold] ({data['type']})  id={data['id']}")
    if data["endpoint"] or data["port"]:
        console.print(f"  endpoint: {data['endpoint']}:{data['port']}")
    if data["description"]:
        console.print(f"  desc: {data['description']}")
    table = Table(title="属性")
    for col in ("key", "value", "type", "standard"):
        table.add_column(col)
    for a in data["attributes"]:
        table.add_row(a["key"], a["value"], a["value_type"], "✓" if a["is_standard"] else "")
    console.print(table)
    if data.get("operations_guide"):
        console.print(f"\n[dim]操作指南:[/dim] {data['operations_guide']}")


@resource_app.command("update")
def resource_update(
    name: Annotated[str, typer.Argument(help="资源 id 或 name")],
    json_output: JsonOption = False,
    endpoint: Annotated[str | None, typer.Option("--endpoint")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    attr: Annotated[list[str] | None, typer.Option("--attr", help="key[:type]=value")] = None,
) -> None:
    """更新资源主表字段或属性."""
    _set_json(json_output)
    attributes = {k: (raw, vt) for k, vt, raw in (_parse_attr(a) for a in attr or [])}
    try:
        with _db() as conn:
            updated = repo.update_resource(
                conn,
                name,
                endpoint=endpoint,
                port=port,
                status=status,
                description=description,
                attributes=attributes or None,
            )
    except KeyError as e:
        _fail(str(e))
    except ValueError as e:
        _fail(str(e))
    if _JSON_FLAG["value"]:
        _emit_json(_masked_resource(updated, reveal=False))
    else:
        console.print(f"[green]✓[/green] 已更新 [bold]{updated['name']}[/bold]")


@resource_app.command("delete")
def resource_delete(
    name: Annotated[str, typer.Argument(help="资源 id 或 name")],
    json_output: JsonOption = False,
    force: Annotated[bool, typer.Option("--force", help="强制删除并级联清理")] = False,
) -> None:
    """删除资源."""
    _set_json(json_output)
    try:
        with _db() as conn:
            repo.delete_resource(conn, name, force=force)
    except KeyError as e:
        _fail(str(e))
    except ValueError as e:
        _fail(str(e))
    if _JSON_FLAG["value"]:
        _emit_json({"deleted": name})
    else:
        console.print(f"[green]✓[/green] 已删除 [bold]{name}[/bold]")


@resource_app.command("types")
def resource_types(json_output: JsonOption = False) -> None:
    """列出所有已注册资源类型."""
    _set_json(json_output)
    types = list_resource_types()
    if _JSON_FLAG["value"]:
        _emit_json(types)
    else:
        for t in types:
            console.print(f"  • {t}")


def _masked_resource(res: dict, reveal: bool) -> dict:
    """对返回给上层的资源 dict 做 secret 脱敏."""
    out = dict(res)
    out["attributes"] = [
        {**a, "value": _mask(a["value"], a["value_type"], reveal)}
        for a in res.get("attributes", [])
    ]
    return out


# ---------- relation ----------


@relation_app.command("add")
def relation_add(
    source: Annotated[str, typer.Option("--source", help="依赖方 (id 或 name)")],
    target: Annotated[str, typer.Option("--target", help="被依赖方 (id 或 name)")],
    json_output: JsonOption = False,
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    """添加依赖关系 (含环路检测)."""
    _set_json(json_output)
    try:
        with _db() as conn:
            rel = repo.add_relation(conn, source=source, target=target, note=note)
    except CycleError as e:
        _fail(str(e))
    except KeyError as e:
        _fail(str(e))
    if _JSON_FLAG["value"]:
        _emit_json(
            {
                "id": rel.id,
                "source": rel.source_id,
                "target": rel.target_id,
                "type": rel.relation_type,
            }
        )
    else:
        console.print(f"[green]✓[/green] {rel.source_id} --{rel.relation_type}--> {rel.target_id}")


@relation_app.command("list")
def relation_list(
    json_output: JsonOption = False,
    resource: Annotated[
        str | None, typer.Option("--resource", help="仅列出涉及该资源的关系")
    ] = None,
) -> None:
    """列出关系."""
    _set_json(json_output)
    try:
        with _db() as conn:
            rels = repo.list_relations(conn, resource=resource)
    except KeyError as e:
        _fail(str(e))
    data = [
        {
            "id": r.id,
            "source": r.source_id,
            "target": r.target_id,
            "type": r.relation_type,
            "note": r.note,
        }
        for r in rels
    ]
    if _JSON_FLAG["value"]:
        _emit_json(data)
        return
    table = Table(title="关系")
    for col in ("source", "type", "target", "note"):
        table.add_column(col)
    for r in data:
        table.add_row(r["source"], r["type"], r["target"], r["note"])
    console.print(table)


@relation_app.command("delete")
def relation_delete(
    source: Annotated[str, typer.Option("--source", help="依赖方 (id 或 name)")],
    target: Annotated[str, typer.Option("--target", help="被依赖方 (id 或 name)")],
    json_output: JsonOption = False,
) -> None:
    """删除一条依赖关系."""
    _set_json(json_output)
    with _db() as conn:
        repo.delete_relation(conn, source=source, target=target)
    if _JSON_FLAG["value"]:
        _emit_json({"deleted": {"source": source, "target": target}})
    else:
        console.print(f"[green]✓[/green] 已删除关系 {source} --depends_on--> {target}")


@relation_app.command("graph")
def relation_graph_cmd(
    name: Annotated[str, typer.Argument(help="资源 id 或 name")],
    json_output: JsonOption = False,
) -> None:
    """查看依赖拓扑."""
    _set_json(json_output)
    try:
        with _db() as conn:
            tree = repo.relation_graph(conn, name)
    except KeyError as e:
        _fail(str(e))
    if _JSON_FLAG["value"]:
        _emit_json(tree)
    else:
        _print_graph(tree, console)


def _print_graph(tree: dict, con: Console) -> None:
    """以缩进树形式打印依赖拓扑 (人类可读)."""
    res = tree["resource"]
    con.print(f"[bold]{res['name']}[/bold] ({res['type']}) 依赖拓扑:")
    if tree["downstream"]:
        con.print("  [cyan]我依赖 (downstream):[/cyan]")
        _print_tree_nodes(tree["downstream"], "depends_on", "    ", con)
    if tree["upstream"]:
        con.print("  [magenta]依赖我的 (upstream):[/magenta]")
        _print_tree_nodes(tree["upstream"], "required_by", "    ", con)
    if not tree["downstream"] and not tree["upstream"]:
        con.print("  [dim]无依赖关系[/dim]")


def _print_tree_nodes(nodes: list[dict], child_key: str, indent: str, con: Console) -> None:
    for n in nodes:
        r = n["resource"]
        rel = n.get("relation_type", "")
        con.print(f"{indent}• [bold]{r['name']}[/bold] ({r['type']}) [dim]--{rel}-->[/dim]")
        _print_tree_nodes(n.get(child_key, []), child_key, indent + "    ", con)


# ---------- concern ----------


@concern_app.command("add")
def concern_add(
    resource: Annotated[str, typer.Option("--resource", help="资源 id 或 name")],
    category: Annotated[str, typer.Option("--category", help="expiry/capacity/renewal/custom")],
    desc: Annotated[str, typer.Option("--desc", help="描述")],
    json_output: JsonOption = False,
    due: Annotated[str | None, typer.Option("--due", help="ISO 8601 到期时间")] = None,
    severity: Annotated[str, typer.Option("--severity", help="info/warning/critical")] = "info",
) -> None:
    """添加关注点."""
    _set_json(json_output)
    try:
        with _db() as conn:
            c = repo.add_concern(
                conn,
                resource=resource,
                category=category,
                description=desc,
                due_at=due,
                severity=severity,
            )
    except KeyError as e:
        _fail(str(e))
    except ValueError as e:
        _fail(str(e))
    if _JSON_FLAG["value"]:
        _emit_json(
            {
                "id": c.id,
                "resource": c.resource_id,
                "category": c.category,
                "desc": c.description,
                "due": c.due_at,
                "severity": c.severity,
                "status": c.status,
            }
        )
    else:
        console.print(f"[green]✓[/green] 关注点 #{c.id} 已添加到 {c.resource_id}")


@concern_app.command("resolve")
def concern_resolve(
    concern_id: Annotated[int, typer.Argument(help="关注点 id")],
    json_output: JsonOption = False,
) -> None:
    """将关注点标记为已解决."""
    _set_json(json_output)
    try:
        with _db() as conn:
            result = repo.resolve_concern(conn, concern_id=concern_id)
    except KeyError as e:
        _fail(str(e))
    if result is None:
        _fail(f"关注点 #{concern_id} 不存在")
    if _JSON_FLAG["value"]:
        _emit_json(
            {
                "id": result.id,
                "resource": result.resource_id,
                "status": result.status,
                "checked_at": result.checked_at,
            }
        )
    else:
        console.print(f"[green]✓[/green] 关注点 #{concern_id} 已解决")


@concern_app.command("list")
def concern_list(
    json_output: JsonOption = False,
    resource: Annotated[str | None, typer.Option("--resource")] = None,
    status: Annotated[str, typer.Option("--status")] = "open",
) -> None:
    """列出关注点."""
    _set_json(json_output)
    try:
        with _db() as conn:
            items = repo.list_concerns(conn, resource=resource, status=status)
    except KeyError as e:
        _fail(str(e))
    data = [
        {
            "id": c.id,
            "resource": c.resource_id,
            "category": c.category,
            "desc": c.description,
            "due": c.due_at,
            "severity": c.severity,
            "status": c.status,
        }
        for c in items
    ]
    if _JSON_FLAG["value"]:
        _emit_json(data)
        return
    table = Table(title="关注点")
    for col in ("id", "resource", "category", "severity", "due", "desc"):
        table.add_column(col)
    for c in data:
        table.add_row(
            str(c["id"]),
            c["resource"],
            c["category"],
            c["severity"],
            str(c["due"]),
            c["desc"],
        )
    console.print(table)


@concern_app.command("due")
def concern_due(
    within: Annotated[str, typer.Option("--within", help="时间窗口: 7d/12h/30m")],
    json_output: JsonOption = False,
) -> None:
    """查询即将到期的 open 关注点 (按展示组连续排序, 含派生 urgency)."""
    _set_json(json_output)
    now = datetime.now(UTC)
    try:
        with _db() as conn:
            items = repo.concerns_due(conn, within=within, now=now)
            # resource_id (UUID) -> 可读资源名映射, 供 name 键与人类表格使用
            names = {r["id"]: r["name"] for r in repo.list_resources(conn)}
    except ValueError as e:
        _fail(str(e))
    data = [
        {
            "id": c.id,
            "resource": c.resource_id,
            "name": names.get(c.resource_id, c.resource_id),
            "category": c.category,
            "desc": c.description,
            "due": c.due_at,
            "severity": c.severity,
            "urgency": repo.urgency_of(c.due_at, now=now),
        }
        for c in items
    ]
    if _JSON_FLAG["value"]:
        _emit_json(data)
        return
    if not data:
        console.print(f"[dim]{within} 内无到期关注点[/dim]")
        return
    table = Table(title=f"{within} 内到期关注点")
    for col in ("id", "name", "urgency", "due", "severity", "desc"):
        table.add_column(col)
    for c in data:
        table.add_row(
            str(c["id"]), c["name"], c["urgency"], str(c["due"]), c["severity"], c["desc"]
        )
    console.print(table)


def main() -> None:
    """opsctl entry point."""
    app()


if __name__ == "__main__":
    main()
