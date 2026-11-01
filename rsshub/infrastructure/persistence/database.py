"""SQLite 连接与迁移。

插件数据落在宿主分配的 data_dir 下（ctx.paths.data_dir），不写宿主目录。
迁移以 schema_migrations 表记录版本，按序执行。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ...errors import RsshubError

# 继承哨兵：字段取该值表示向上继承
INHERIT_VALUE = -100
INHERIT_MODE = "inherit"

SCHEMA_VERSION = "2"

_DDL_V1 = """
CREATE TABLE IF NOT EXISTS rsshub_users (
    user_id TEXT PRIMARY KEY,
    state INTEGER NOT NULL DEFAULT 1,
    interval_minutes INTEGER NOT NULL DEFAULT -100,
    notify INTEGER NOT NULL DEFAULT -100,
    push_mode TEXT NOT NULL DEFAULT 'inherit',
    handlers_mode TEXT NOT NULL DEFAULT 'inherit',
    handlers TEXT NOT NULL DEFAULT '[]',
    length_limit INTEGER NOT NULL DEFAULT -100,
    display_author INTEGER NOT NULL DEFAULT -100,
    display_via INTEGER NOT NULL DEFAULT -100,
    display_title INTEGER NOT NULL DEFAULT -100,
    display_entry_tags INTEGER NOT NULL DEFAULT -100,
    style INTEGER NOT NULL DEFAULT -100,
    display_media INTEGER NOT NULL DEFAULT -100,
    default_target_session TEXT,
    needs_binding_notice INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rsshub_feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state INTEGER NOT NULL DEFAULT 1,
    link TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    etag TEXT,
    last_modified TEXT,
    entry_hashes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rsshub_subs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state INTEGER NOT NULL DEFAULT 1,
    user_id TEXT NOT NULL,
    feed_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    target_session TEXT,
    platform_name TEXT,
    interval_minutes INTEGER NOT NULL DEFAULT -100,
    next_check_at TEXT,
    notify INTEGER NOT NULL DEFAULT -100,
    push_mode TEXT NOT NULL DEFAULT 'inherit',
    handlers_mode TEXT NOT NULL DEFAULT 'inherit',
    handlers TEXT NOT NULL DEFAULT '[]',
    length_limit INTEGER NOT NULL DEFAULT -100,
    display_author INTEGER NOT NULL DEFAULT -100,
    display_via INTEGER NOT NULL DEFAULT -100,
    display_title INTEGER NOT NULL DEFAULT -100,
    display_entry_tags INTEGER NOT NULL DEFAULT -100,
    style INTEGER NOT NULL DEFAULT -100,
    display_media INTEGER NOT NULL DEFAULT -100,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (feed_id) REFERENCES rsshub_feeds(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_subs_feed ON rsshub_subs(feed_id);
CREATE INDEX IF NOT EXISTS idx_subs_session ON rsshub_subs(target_session);

CREATE TABLE IF NOT EXISTS rsshub_session_defaults (
    session_key TEXT PRIMARY KEY,
    defaults TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rsshub_push_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sub_id INTEGER,
    user_id TEXT NOT NULL,
    feed_id INTEGER,
    source_type TEXT NOT NULL DEFAULT 'feed',
    source_key TEXT,
    content TEXT NOT NULL DEFAULT '',
    raw_xml TEXT,
    media_urls TEXT,
    handler_trace TEXT,
    entry_title TEXT NOT NULL DEFAULT '',
    entry_link TEXT NOT NULL DEFAULT '',
    entry_guid TEXT,
    feed_title TEXT NOT NULL DEFAULT '',
    feed_link TEXT NOT NULL DEFAULT '',
    platform_name TEXT,
    target_session TEXT,
    status TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    fail_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_session ON rsshub_push_history(target_session);
CREATE INDEX IF NOT EXISTS idx_history_status ON rsshub_push_history(status);
CREATE INDEX IF NOT EXISTS idx_history_entry ON rsshub_push_history(entry_link);

CREATE TABLE IF NOT EXISTS rsshub_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);
"""


def utcnow() -> str:
    """当前 UTC 时间（ISO 8601，秒级）。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


_DDL_V2 = """
ALTER TABLE rsshub_subs ADD COLUMN session_label TEXT;
"""

# 版本化迁移：新库按顺序执行，老库只补未应用的步骤。
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("1", _DDL_V1, "初始表结构"),
    ("2", _DDL_V2, "订阅表增加会话显示名"),
)


class Database:
    """SQLite 连接封装。

    写操作走同一条连接；Runner 内单线程 asyncio，SQLite 本地文件操作在微秒级，
    直接同步执行即可，不做线程池绕行。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    def connect(self) -> None:
        if self._conn is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RsshubError("数据库尚未连接")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def migrate(self) -> None:
        """按版本顺序执行未应用的迁移。"""
        self.connect()
        conn = self.conn
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS rsshub_schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL,"
            "description TEXT NOT NULL DEFAULT '')"
        )
        applied = {row["version"] for row in conn.execute("SELECT version FROM rsshub_schema_migrations")}
        for version, script, description in _MIGRATIONS:
            if version in applied:
                continue
            conn.executescript(script)
            conn.execute(
                "INSERT INTO rsshub_schema_migrations(version, applied_at, description) VALUES (?,?,?)",
                (version, utcnow(), description),
            )
            conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params))

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        cursor = self.conn.execute(sql, params)
        return cursor.fetchone()

    def execute(self, sql: str, params: tuple = ()) -> int:
        cursor = self.conn.execute(sql, params)
        self.conn.commit()
        return cursor.lastrowid or 0

    def execute_many(self, sql: str, seq: list[tuple]) -> None:
        self.conn.executemany(sql, seq)
        self.conn.commit()
