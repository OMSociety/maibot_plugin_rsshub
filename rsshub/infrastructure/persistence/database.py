"""SQLite 连接与建表。

插件数据落在宿主分配的 data_dir 下（ctx.paths.data_dir），不写宿主目录。
建表脚本全部带 IF NOT EXISTS，每次打开都执行一次；已有库缺的列按建表脚本补齐，
所以升级插件时不需要手工改表。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ...errors import RsshubError

# 继承哨兵：字段取该值表示向上继承
INHERIT_VALUE = -100
INHERIT_MODE = "inherit"

_SCHEMA = """
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
    session_label TEXT,
    interval_minutes INTEGER NOT NULL DEFAULT -100,
    next_check_at TEXT,
    fail_count INTEGER NOT NULL DEFAULT 0,
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
    next_retry_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_session ON rsshub_push_history(target_session);
CREATE INDEX IF NOT EXISTS idx_history_status ON rsshub_push_history(status);
CREATE INDEX IF NOT EXISTS idx_history_entry ON rsshub_push_history(entry_link);
CREATE INDEX IF NOT EXISTS idx_history_retry ON rsshub_push_history(status, next_retry_at);
"""


def utcnow() -> str:
    """当前 UTC 时间（ISO 8601，秒级）。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _split_schema(schema: str) -> tuple[str, str]:
    """把建表脚本拆成「建表」与「建索引」两部分。

    老库的表不会被 IF NOT EXISTS 重建，缺列要等补列之后才存在；
    索引一旦引用新列，就必须排在补列之后执行。
    """
    tables: list[str] = []
    indexes: list[str] = []
    for raw in schema.split(";"):
        statement = raw.strip()
        if not statement:
            continue
        (indexes if statement.upper().startswith("CREATE INDEX") else tables).append(statement)
    return ";\n".join(tables) + ";", ";\n".join(indexes) + ";"


_TABLES_SQL, _INDEXES_SQL = _split_schema(_SCHEMA)


def _schema_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'rsshub\\_%' ESCAPE '\\'")
    return [row[0] for row in rows]


def _reconcile_columns(conn: sqlite3.Connection) -> list[str]:
    """按建表脚本给老库补上缺的列，返回补了哪些（`表.列`）。

    只做加列：已有列的类型与默认值不比对、不修改，已有数据不动。
    SQLite 的 ADD COLUMN 不接受没有默认值的 NOT NULL 列，遇到这种新列直接报错，
    让升级者决定怎么填历史数据，而不是静默失败。
    """
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(_SCHEMA)
        added: list[str] = []
        for table in _schema_tables(reference):
            expected = reference.execute(f"PRAGMA table_info({table})").fetchall()
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for _cid, name, ctype, notnull, default, primary in expected:
                if name in existing or primary:
                    continue
                if notnull and default is None:
                    raise RsshubError(
                        f"表 {table} 新增的必填列 {name} 没有默认值，无法自动补齐；请先手工迁移历史数据再启动插件"
                    )
                ddl = f"ALTER TABLE {table} ADD COLUMN {name} {ctype}"
                if notnull:
                    ddl += " NOT NULL"
                if default is not None:
                    ddl += f" DEFAULT {default}"
                conn.execute(ddl)
                added.append(f"{table}.{name}")
        return added
    finally:
        reference.close()


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

    def ensure_schema(self) -> list[str]:
        """建表并补齐老库缺的列，返回补了哪些列（`表.列`）。"""
        self.connect()
        self.conn.executescript(_TABLES_SQL)
        added = _reconcile_columns(self.conn)
        self.conn.executescript(_INDEXES_SQL)
        self.conn.commit()
        return added

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
