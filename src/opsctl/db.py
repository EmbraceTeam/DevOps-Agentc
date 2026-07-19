"""SQLite schema 与连接管理.

四张表 (见 spec Design Notes):
- resources: 公共高频字段 (id/name/type/endpoint/port/status/...)
- resource_attributes: 类型特定字段 + 扩展字段 (is_standard/value_type)
- relations: 只管依赖 (source->target)
- concerns: 关注点 (挂资源上, 支持 due_at 到期查询)

DB 路径默认 ``data/opsctl.db``, 可被 ``OPSCTL_DB`` 环境变量覆盖; 单测可通过
传入 ``db_url=...`` 直接用 in-memory 或临时文件.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/opsctl.db")
DB_ENV_VAR = "OPSCTL_DB"

_SCHEMA = """
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
"""


def resolve_db_url(db_url: str | None = None) -> str:
    """解析最终 DB URL, 优先级: 显式参数 > 环境变量 > 默认路径."""
    if db_url:
        return db_url
    env_val = os.environ.get(DB_ENV_VAR)
    if env_val:
        return env_val
    return str(DEFAULT_DB_PATH)


def get_db(db_url: str | None = None) -> sqlite3.Connection:
    """打开/创建 SQLite 连接并初始化 schema.

    返回的连接已启用 ``PRAGMA foreign_keys`` 与 ``row_factory = Row``.
    对于文件型 DB, 父目录会被自动创建.
    """
    url = resolve_db_url(db_url)
    if url != ":memory:":
        Path(url).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(url)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn
