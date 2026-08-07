"""数据访问层: CRUD + 环路检测 + 级联检查 + 到期查询.

全部为接收 ``sqlite3.Connection`` 的纯函数, 便于单测用 in-memory DB.
不直接依赖 Resource 子类实例 — 只用注册表做类型与必填校验.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import (
    Concern,
    CycleError,
    Relation,
    ResourceTypeNotFoundError,
    get_resource_class,
    list_resource_types,
)

# ---------- 工具 ----------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# d/h/m 各单位的窗口上限, 防 OverflowError (等价 3650 天)
_WITHIN_LIMITS = {"d": 365 * 10, "h": 365 * 10 * 24, "m": 365 * 10 * 24 * 60}


def _parse_due_within(within: str, *, now: datetime | None = None) -> datetime:
    """解析 `7d`/`12h`/`30m` 形式, 返回"现在 + N" 的 UTC datetime.

    拒绝负数与超大输入 (防 OverflowError 与"已逾期"误判).
    可选 ``now`` 供调用方冻结时钟, 保证窗口过滤与排序/标注同一时刻.
    """
    if not isinstance(within, str) or len(within) < 2:
        raise ValueError(f"非法 --within 值 '{within}', 示例: 7d, 12h, 30m")
    unit = within[-1].lower()
    try:
        amount = int(within[:-1])
    except ValueError as exc:
        raise ValueError(f"非法 --within 值 '{within}'") from exc
    if amount < 0:
        raise ValueError(f"--within 不允许负数 '{within}'")
    if unit not in _WITHIN_LIMITS:
        raise ValueError(f"非法 --within 单位 '{unit}', 支持 d/h/m (大小写不敏感)")
    if amount > _WITHIN_LIMITS[unit]:
        raise ValueError(f"--within 上限 {_WITHIN_LIMITS[unit]}{unit}")
    delta = {
        "d": timedelta(days=amount),
        "h": timedelta(hours=amount),
        "m": timedelta(minutes=amount),
    }[unit]
    base = now if now is not None else datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    return base + delta


def urgency_of(due_at: str | None, *, now: datetime | None = None) -> str:
    """派生 urgency 标签: urgent=≤now+24h (含已过期), soon=≤now+7d, later=其余.

    纯函数, 不改数据. due_at 为 None 或解析失败一律返回 ``later``;
    naive 的 now/due_at 均视为 UTC. 阈值固定 24h/7d (spec 冻结).
    """
    if due_at is None:
        return "later"
    try:
        dt = datetime.fromisoformat(due_at)
    except (TypeError, ValueError):
        return "later"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)
    if dt <= now + timedelta(hours=24):
        return "urgent"
    if dt <= now + timedelta(days=7):
        return "soon"
    return "later"


# ---------- 资源 CRUD ----------


def create_resource(
    conn: sqlite3.Connection,
    *,
    type_name: str,
    name: str,
    endpoint: str | None = None,
    port: int | None = None,
    status: str | None = "active",
    description: str | None = None,
    attributes: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """创建资源 + 其标准/扩展属性.

    ``attributes`` 形如 ``{"host": ("10.0.0.1", "str"), "password": ("x", "secret")}``,
    key 对应属性名, value 是 (raw_value, value_type) 元组.

    必填标准字段缺失则抛 ``ValueError``.
    """
    try:
        cls = get_resource_class(type_name)
    except ResourceTypeNotFoundError:
        raise
    spec = cls.resolved_standard_attributes()

    attributes = attributes or {}
    # 校验必填
    missing = [
        n for n, s in spec.items() if s.required and attributes.get(n, (None,))[0] in (None, "")
    ]
    # default 兜底
    for n, s in spec.items():
        if n in attributes:
            continue
        if s.default is not None:
            attributes[n] = (s.default, s.type)
    # 再次校验 (default 补齐后)
    missing = [
        n
        for n, s in spec.items()
        if s.required and (n not in attributes or attributes[n][0] in (None, ""))
    ]
    if missing:
        raise ValueError(f"缺少必填标准字段: {missing}")

    rid = str(uuid.uuid4())
    now = _now_iso()
    conn.execute(
        """INSERT INTO resources (id, name, type, status, endpoint, port, description,
           created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (rid, name, type_name, status, endpoint, port, description, now, now),
    )
    for key, (raw, vtype) in attributes.items():
        is_std = 1 if key in spec else 0
        # 标准字段的 value_type 由 schema 决定, 忽略用户传入的 vtype (防篡改)
        resolved_type = spec[key].type if key in spec else (vtype or "str")
        conn.execute(
            """INSERT INTO resource_attributes (resource_id, key, value, value_type, is_standard)
               VALUES (?,?,?,?,?)""",
            (rid, key, _stringify(raw, resolved_type, key), resolved_type, is_std),
        )
    # 自动创建资源类型的默认关注点
    for concern in cls.default_concerns:
        conn.execute(
            """INSERT INTO concerns (resource_id, category, description, severity, status,
               created_at) VALUES (?,?,?,?,?,?)""",
            (rid, concern.category, concern.description, concern.severity, "open", now),
        )
    conn.commit()
    return get_resource(conn, rid)


def _stringify(value: Any, vtype: str, key: str = "") -> str:
    """把 raw value 按 vtype 转成存储字符串.

    解析失败抛带 key 的 ValueError (满足 spec I/O 矩阵"解析失败→报错").
    json 类型即使是字符串也用 json.loads 校验合法性.
    """
    if value is None:
        return ""
    label = f"属性 '{key}'" if key else "属性"
    if vtype == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        return "true" if str(value).lower() in ("true", "1", "yes") else "false"
    if vtype == "int":
        try:
            return str(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} 声明为 int 但值 '{value}' 无法解析: {exc}") from exc
    if vtype == "json":
        import json

        if isinstance(value, str):
            try:
                json.loads(value)  # 校验合法性
            except json.JSONDecodeError as exc:
                raise ValueError(f"{label} 声明为 json 但值不是合法 JSON: {exc}") from exc
            return value
        return json.dumps(value)
    return str(value)


def list_resources(
    conn: sqlite3.Connection, type_filter: str | None = None
) -> list[dict[str, Any]]:
    """列出资源 (主表字段), 可按 type 筛选. 不带属性 (避免 N+1)."""
    if type_filter is not None and type_filter not in list_resource_types():
        raise ResourceTypeNotFoundError(f"未注册的资源类型 '{type_filter}'")
    sql = "SELECT * FROM resources"
    params: tuple[Any, ...] = ()
    if type_filter:
        sql += " WHERE type = ?"
        params = (type_filter,)
    sql += " ORDER BY name"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_resource(conn: sqlite3.Connection, id_or_name: str) -> dict[str, Any]:
    """按 id 或 name 取单个资源, 含属性. 找不到抛 KeyError."""
    row = conn.execute(
        "SELECT * FROM resources WHERE id = ? OR name = ?", (id_or_name, id_or_name)
    ).fetchone()
    if row is None:
        raise KeyError(f"资源 '{id_or_name}' 不存在")
    base = dict(row)
    attrs = conn.execute(
        "SELECT key, value, value_type, is_standard FROM resource_attributes WHERE resource_id = ?",
        (base["id"],),
    ).fetchall()
    base["attributes"] = [dict(a) for a in attrs]
    base["operations_guide"] = get_resource_class(base["type"]).operations_guide
    return base


def update_resource(
    conn: sqlite3.Connection,
    id_or_name: str,
    *,
    endpoint: str | None = None,
    port: int | None = None,
    status: str | None = None,
    description: str | None = None,
    attributes: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """更新资源主表字段与/或属性 (扩展属性 is_standard=false)."""
    current = get_resource(conn, id_or_name)
    rid = current["id"]
    spec = get_resource_class(current["type"]).resolved_standard_attributes()

    fields: dict[str, Any] = {}
    for col, val in [
        ("endpoint", endpoint),
        ("port", port),
        ("status", status),
        ("description", description),
    ]:
        if val is not None:
            fields[col] = val
    fields["updated_at"] = _now_iso()
    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE resources SET {set_clause} WHERE id = ?", (*fields.values(), rid))
    for key, (raw, vtype) in (attributes or {}).items():
        # 标准字段类型由 schema 决定; 扩展字段用用户传入或默认 str
        resolved_type = spec[key].type if key in spec else (vtype or "str")
        is_std = 1 if key in spec else 0
        conn.execute(
            """INSERT INTO resource_attributes (resource_id, key, value, value_type, is_standard)
               VALUES (?,?,?,?,?)
               ON CONFLICT(resource_id, key) DO UPDATE SET
                 value=excluded.value, value_type=excluded.value_type,
                 is_standard=excluded.is_standard""",
            (rid, key, _stringify(raw, resolved_type, key), resolved_type, is_std),
        )
    # 重新校验必填标准字段未被清空 (update 也守 spec Boundaries 的必填约束)
    if attributes:
        final = get_resource(conn, rid)
        attr_map = {a["key"]: a["value"] for a in final["attributes"]}
        missing = [n for n, s in spec.items() if s.required and not attr_map.get(n)]
        if missing:
            raise ValueError(f"更新后缺少必填标准字段: {missing}")
    conn.commit()
    return get_resource(conn, rid)


def delete_resource(conn: sqlite3.Connection, id_or_name: str, *, force: bool = False) -> None:
    """删除资源. 若被关系引用且未 force, 抛 ``ValueError`` 列出引用方."""
    current = get_resource(conn, id_or_name)
    rid = current["id"]
    referrers = [
        dict(r)
        for r in conn.execute(
            """SELECT r.id, r.source_id, r.relation_type FROM relations r
               WHERE r.target_id = ?""",
            (rid,),
        )
    ]
    if referrers and not force:
        names = [r["source_id"] for r in referrers]
        raise ValueError(f"资源被 {len(referrers)} 条关系引用 (来源: {names}); 加 --force 强制删除")
    conn.execute("DELETE FROM resources WHERE id = ?", (rid,))
    conn.commit()


# ---------- 关系 CRUD + 环路检测 ----------


def _resource_exists(conn: sqlite3.Connection, id_or_name: str) -> str:
    row = conn.execute(
        "SELECT id FROM resources WHERE id = ? OR name = ?", (id_or_name, id_or_name)
    ).fetchone()
    if row is None:
        raise KeyError(f"资源 '{id_or_name}' 不存在")
    return row["id"]


def add_relation(
    conn: sqlite3.Connection,
    *,
    source: str,
    target: str,
    relation_type: str = "depends_on",
    note: str = "",
) -> Relation:
    """添加关系, 拒绝循环依赖.

    环路检测: 若 target -> ... -> source 已存在路径, 则新加 source->target 后会形成环.
    关系类型仅支持 ``depends_on``.
    """
    if relation_type not in {"depends_on"}:
        raise ValueError(f"关系类型仅支持 depends_on, 收到 '{relation_type}'")
    source_id = _resource_exists(conn, source)
    target_id = _resource_exists(conn, target)
    # 检测: 从 target 出发能否到达 source; 若能, 加 source->target 即成环
    path = _find_path(conn, target_id, source_id)
    if path is not None:
        cycle = path + [source_id]
        raise CycleError(cycle)

    now = _now_iso()
    try:
        cur = conn.execute(
            """INSERT INTO relations (source_id, target_id, relation_type, note, created_at)
               VALUES (?,?,?,?,?)""",
            (source_id, target_id, relation_type, note, now),
        )
        rid = cur.lastrowid
    except sqlite3.IntegrityError:
        # (source, target, type) 已存在 (UNIQUE 约束) — 幂等返回现有关系
        row = conn.execute(
            """SELECT id, note, created_at FROM relations
               WHERE source_id=? AND target_id=? AND relation_type=?""",
            (source_id, target_id, relation_type),
        ).fetchone()
        rid = row["id"]
        note = row["note"]
        now = row["created_at"]
    conn.commit()
    return Relation(
        id=rid,
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        note=note,
        created_at=now,
    )


def delete_relation(
    conn: sqlite3.Connection,
    *,
    source: str,
    target: str,
) -> None:
    """删除一条关系 (source 依赖 target). 不存在则静默忽略."""
    try:
        source_id = _resource_exists(conn, source)
        target_id = _resource_exists(conn, target)
    except KeyError:
        return  # 资源不存在, 关系自然不存在
    conn.execute(
        """DELETE FROM relations WHERE source_id = ? AND target_id = ?""",
        (source_id, target_id),
    )
    conn.commit()


def _find_path(conn: sqlite3.Connection, start: str, goal: str) -> list[str] | None:
    """DFS: 沿 source->target 方向找 start 到 goal 的路径, 无则 None."""
    if start == goal:
        return [start]
    stack: list[tuple[str, list[str]]] = [(start, [start])]
    visited: set[str] = {start}
    while stack:
        node, path = stack.pop()
        rows = conn.execute(
            "SELECT target_id FROM relations WHERE source_id = ?", (node,)
        ).fetchall()
        for r in rows:
            nxt = r["target_id"]
            if nxt == goal:
                return path + [nxt]
            if nxt not in visited:
                visited.add(nxt)
                stack.append((nxt, path + [nxt]))
    return None


def list_relations(conn: sqlite3.Connection, resource: str | None = None) -> list[Relation]:
    sql = "SELECT id, source_id, target_id, relation_type, note, created_at FROM relations"
    params: tuple[Any, ...] = ()
    if resource:
        rid = _resource_exists(conn, resource)
        sql += " WHERE source_id = ? OR target_id = ?"
        params = (rid, rid)
    sql += " ORDER BY created_at"
    rows = conn.execute(sql, params).fetchall()
    return [
        Relation(
            id=r["id"],
            source_id=r["source_id"],
            target_id=r["target_id"],
            relation_type=r["relation_type"],
            note=r["note"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


def relation_graph(conn: sqlite3.Connection, id_or_name: str) -> dict[str, Any]:
    """返回某资源的上下游依赖树.

    结构: ``{"resource": {...}, "downstream": [...], "upstream": [...]}``.
    downstream = 我依赖的资源 (我作为 source 的关系指向的 target).
    upstream = 依赖我的资源 (我作为 target 的关系的来源 source).

    walk 函数带 visited 集合, 即使数据中存在绕过 add_relation 写入的环
    也不会无限递归.
    """
    rid = _resource_exists(conn, id_or_name)
    base = get_resource(conn, rid)

    def _walk_down(node: str, visited: set[str]) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT target_id, relation_type FROM relations WHERE source_id = ?", (node,)
        ).fetchall()
        out = []
        for r in rows:
            child = get_resource(conn, r["target_id"])
            if child["id"] in visited:
                continue
            visited.add(child["id"])
            out.append(
                {
                    "resource": {"id": child["id"], "name": child["name"], "type": child["type"]},
                    "relation_type": r["relation_type"],
                    "depends_on": _walk_down(child["id"], visited),
                }
            )
        return out

    def _walk_up(node: str, visited: set[str]) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT source_id, relation_type FROM relations WHERE target_id = ?", (node,)
        ).fetchall()
        out = []
        for r in rows:
            parent = get_resource(conn, r["source_id"])
            if parent["id"] in visited:
                continue
            visited.add(parent["id"])
            out.append(
                {
                    "resource": {
                        "id": parent["id"],
                        "name": parent["name"],
                        "type": parent["type"],
                    },
                    "relation_type": r["relation_type"],
                    "required_by": _walk_up(parent["id"], visited),
                }
            )
        return out

    return {
        "resource": {"id": base["id"], "name": base["name"], "type": base["type"]},
        "downstream": _walk_down(rid, {rid}),
        "upstream": _walk_up(rid, {rid}),
    }


# ---------- 关注点 CRUD + 到期查询 ----------


VALID_SEVERITIES = frozenset({"info", "warning", "critical"})

# 展示排序权重 (未知值一律 .get(..., 99) 防御, 脏数据不崩溃)
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
_URGENCY_ORDER = {"urgent": 0, "soon": 1, "later": 2}


def _normalize_due_at(due_at: str) -> str:
    """校验并归一化 due_at 为带 UTC 偏移的 ISO 字符串.

    规范化存储避免字符串比较时混合时区/格式导致的排序错乱.
    naive datetime 视为 UTC.
    """
    try:
        dt = datetime.fromisoformat(due_at)
    except ValueError as exc:
        raise ValueError(f"非法 due_at 格式 '{due_at}', 应为 ISO 8601") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def add_concern(
    conn: sqlite3.Connection,
    *,
    resource: str,
    category: str,
    description: str,
    due_at: str | None = None,
    severity: str = "info",
) -> Concern:
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"非法 severity '{severity}', 允许: {sorted(VALID_SEVERITIES)}")
    if not category or not description:
        raise ValueError("category 与 description 不能为空")
    rid = _resource_exists(conn, resource)
    normalized_due = _normalize_due_at(due_at) if due_at else None
    cur = conn.execute(
        """INSERT INTO concerns (resource_id, category, description, due_at, severity,
           status, created_at) VALUES (?,?,?,?,?,?,?)""",
        (rid, category, description, normalized_due, severity, "open", _now_iso()),
    )
    conn.commit()
    return Concern(
        id=cur.lastrowid,
        resource_id=rid,
        category=category,
        description=description,
        due_at=normalized_due,
        severity=severity,
        status="open",
    )


def list_concerns(
    conn: sqlite3.Connection, resource: str | None = None, status: str = "open"
) -> list[Concern]:
    sql = "SELECT * FROM concerns WHERE status = ?"
    params: list[Any] = [status]
    if resource:
        rid = _resource_exists(conn, resource)
        sql += " AND resource_id = ?"
        params.append(rid)
    sql += " ORDER BY due_at NULLS LAST"
    rows = conn.execute(sql, params).fetchall()
    return [
        Concern(
            id=r["id"],
            resource_id=r["resource_id"],
            category=r["category"],
            description=r["description"],
            due_at=r["due_at"],
            severity=r["severity"],
            checked_at=r["checked_at"],
            status=r["status"],
        )
        for r in rows
    ]


def resolve_concern(
    conn: sqlite3.Connection,
    *,
    concern_id: int,
) -> Concern | None:
    """将关注点状态设为 resolved, 记录 checked_at. 不存在则返回 None."""
    row = conn.execute("SELECT * FROM concerns WHERE id = ?", (concern_id,)).fetchone()
    if row is None:
        return None
    now = _now_iso()
    conn.execute(
        """UPDATE concerns SET status = 'resolved', checked_at = ? WHERE id = ?""",
        (now, concern_id),
    )
    conn.commit()
    return Concern(
        id=row["id"],
        resource_id=row["resource_id"],
        category=row["category"],
        description=row["description"],
        due_at=row["due_at"],
        severity=row["severity"],
        checked_at=now,
        status="resolved",
    )


def concerns_due(
    conn: sqlite3.Connection, within: str, *, now: datetime | None = None
) -> list[Concern]:
    """查询 within 时间窗口内到期的 open 关注点, 按展示组连续排序.

    展示组: 组1=critical|urgent 整体在前 (critical 永不落折叠段),
    组2=soon 且非 critical, 组3=其余; 组内 urgency → severity → due_at 升序.
    窗口过滤与排序/urgency 标注共享同一 ``now`` (冻结时钟结果确定).
    """
    threshold = _parse_due_within(within, now=now)
    rows = conn.execute(
        """SELECT * FROM concerns
           WHERE status = 'open' AND due_at IS NOT NULL AND due_at <= ?
           ORDER BY due_at""",
        (threshold.isoformat(timespec="seconds"),),
    ).fetchall()

    def _group(c: Concern) -> int:
        sev_rank = _SEVERITY_ORDER.get(c.severity, 99)
        urg_rank = _URGENCY_ORDER.get(urgency_of(c.due_at, now=now), 99)
        if sev_rank == 0 or urg_rank == 0:
            return 1
        if urg_rank == 1:
            return 2
        return 3

    items = [
        Concern(
            id=r["id"],
            resource_id=r["resource_id"],
            category=r["category"],
            description=r["description"],
            due_at=r["due_at"],
            severity=r["severity"],
            checked_at=r["checked_at"],
            status=r["status"],
        )
        for r in rows
    ]
    items.sort(
        key=lambda c: (
            _group(c),
            _URGENCY_ORDER.get(urgency_of(c.due_at, now=now), 99),
            _SEVERITY_ORDER.get(c.severity, 99),
            c.due_at or "",
        )
    )
    return items
