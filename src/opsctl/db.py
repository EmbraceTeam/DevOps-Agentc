"""SQLite schema 与连接管理 + 自动迁移.

四张表 (见 spec Design Notes):
- resources: 公共高频字段 (id/name/type/endpoint/port/status/...)
- resource_attributes: 类型特定字段 + 扩展字段 (is_standard/value_type)
- relations: 只管依赖 (source->target)
- concerns: 关注点 (挂资源上, 支持 due_at 到期查询)

自动迁移: ``get_db()`` 每次连接时检查 schema 版本号, 按需执行未应用的迁移.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/opsctl.db")
DB_ENV_VAR = "OPSCTL_DB"

# 当前 schema 版本号 — 每次修改 _SCHEMA 或 _MIGRATIONS 时 +1
SCHEMA_VERSION = 1

_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    type        TEXT NOT NULL,
    status      TEXT,
    endpoint    TEXT,
    port        INTEGER,
    description TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_attributes (
    resource_id TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    value_type  TEXT NOT NULL,
    is_standard INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (resource_id, key),
    FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS relations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'depends_on',
    note          TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    UNIQUE (source_id, target_id, relation_type),
    FOREIGN KEY (source_id) REFERENCES resources(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES resources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS concerns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id TEXT NOT NULL,
    category    TEXT NOT NULL,
    description TEXT NOT NULL,
    due_at      TEXT,
    severity    TEXT DEFAULT 'info',
    checked_at  TEXT,
    status      TEXT DEFAULT 'open',
    created_at  TEXT NOT NULL,
    FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS _schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
"""


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------- 迁移定义 ----------
# 每条迁移: (version, description, function(conn))
# function 接收连接, 执行 ALTER / 重建 等操作.
# 版本号必须连续递增, 不可跳跃.


def _migrate_0_to_1(conn: sqlite3.Connection) -> None:
    """基线迁移: 确保 relations 表有 UNIQUE 约束 (旧库可能缺少)."""
    # SQLite 不支持 ALTER TABLE ADD CONSTRAINT, 需重建表.
    # 用 Python 控制迁移逻辑: 读旧数据 → 删旧表 → 建新表 → 写回
    rows = [dict(r) for r in conn.execute("SELECT * FROM relations").fetchall()]
    conn.execute("DROP TABLE IF EXISTS relations")
    conn.execute(
        """CREATE TABLE relations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id     TEXT NOT NULL,
            target_id     TEXT NOT NULL,
            relation_type TEXT NOT NULL DEFAULT 'depends_on',
            note          TEXT DEFAULT '',
            created_at    TEXT NOT NULL,
            UNIQUE (source_id, target_id, relation_type),
            FOREIGN KEY (source_id) REFERENCES resources(id) ON DELETE CASCADE,
            FOREIGN KEY (target_id) REFERENCES resources(id) ON DELETE CASCADE
        )"""
    )
    for r in rows:
        conn.execute(
            """INSERT INTO relations (id, source_id, target_id, relation_type, note, created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                r["id"],
                r["source_id"],
                r["target_id"],
                r.get("relation_type", "depends_on"),
                r.get("note", ""),
                r.get("created_at", _now_iso()),
            ),
        )
    conn.commit()


_MIGRATIONS: list[tuple[int, str, object]] = [
    (1, "基线: 确保 relations 表 UNIQUE 约束", _migrate_0_to_1),
]


def _get_current_version(conn: sqlite3.Connection) -> int:
    """查询已应用的最高版本号."""
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM _schema_version").fetchone()
    return row[0] if row else 0


def _apply_pending(conn: sqlite3.Connection) -> None:
    """按序执行未应用的迁移."""
    current = _get_current_version(conn)
    for ver, _desc, func in sorted(_MIGRATIONS, key=lambda x: x[0]):
        if ver <= current:
            continue
        func(conn)
        conn.execute(
            "INSERT INTO _schema_version (version, applied_at) VALUES (?, ?)",
            (ver, _now_iso()),
        )
        conn.commit()


def resolve_db_url(db_url: str | None = None) -> str:
    """解析最终 DB URL, 优先级: 显式参数 > 环境变量 > 默认路径."""
    if db_url:
        return db_url
    env_val = os.environ.get(DB_ENV_VAR)
    if env_val:
        return env_val
    return str(DEFAULT_DB_PATH)


def get_db(db_url: str | None = None) -> sqlite3.Connection:
    """打开/创建 SQLite 连接, 初始化 schema 并自动迁移.

    返回的连接已启用 ``PRAGMA foreign_keys`` 与 ``row_factory = Row``.
    对于文件型 DB, 父目录会被自动创建.
    """
    url = resolve_db_url(db_url)
    if url != ":memory:":
        Path(url).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(url)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_BASE_SCHEMA)
    conn.commit()
    _apply_pending(conn)
    return conn
