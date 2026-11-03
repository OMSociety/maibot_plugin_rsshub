"""数据库迁移与会话显示名。"""

from __future__ import annotations

import sqlite3

import pytest

from rsshub.application.services.subscription_service import SubscriptionService, _session_label
from rsshub.infrastructure.persistence.database import _DDL_V1, _MIGRATIONS, Database
from rsshub.infrastructure.persistence.store import Store
from tests.support import FakeClient, StubCtx, make_config

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>G</title><link>https://e.com</link>
<item><title>T</title><link>https://e.com/1</link><guid>g1</guid></item></channel></rss>"""

# 0.4.0 时期真实库里的订阅表：没有 session_label，用它验证升级路径。
_FROZEN_V1_SUBS = """
CREATE TABLE rsshub_subs (
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
    updated_at TEXT NOT NULL
);
"""


def test_v1_ddl_has_no_session_label() -> None:
    """迁移只能加列一次：初始表结构里不许出现 session_label。"""
    assert "session_label" not in _DDL_V1
    assert _MIGRATIONS[0][0] == "1" and _MIGRATIONS[1][0] == "2"


def test_session_label_prefers_group_then_private() -> None:
    assert _session_label("qq", "123456789", "1024", "hash") == "QQ群:1024"
    assert _session_label("qq", "123456789", "", "hash") == "QQ私聊:123456789"
    assert _session_label("", "123456789", "", "hash") == "hash"


def test_migration_upgrades_v1_database(tmp_path: object) -> None:
    path = tmp_path / "old.db"  # type: ignore[operator]
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE rsshub_schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL,"
        "description TEXT NOT NULL DEFAULT '')"
    )
    conn.executescript(_FROZEN_V1_SUBS)
    conn.execute(
        "INSERT INTO rsshub_schema_migrations(version, applied_at, description) "
        "VALUES ('1','2026-01-01T00:00:00+00:00','初始')"
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.migrate()
    columns = {row[1] for row in db.conn.execute("PRAGMA table_info(rsshub_subs)")}
    versions = {row["version"] for row in db.conn.execute("SELECT version FROM rsshub_schema_migrations")}
    db.close()

    assert "session_label" in columns
    assert versions == {"1", "2"}


@pytest.mark.asyncio
async def test_subscribe_records_readable_session(tmp_path: object) -> None:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    store = Store.open(tmp_path / "rsshub.db")  # type: ignore[operator]
    service = SubscriptionService(ctx, store, make_config(), FakeClient(content=RSS))

    result = await service.subscribe(
        user_id="u1",
        raw_link="https://e.com/feed.xml",
        stream_id="hash-session",
        platform="qq",
        group_id="1024",
    )
    sub = result.sub

    assert sub.target_session == "hash-session"
    assert sub.session_label == "QQ群:1024"
    assert store.repos.subs.get(sub.id).session_label == "QQ群:1024"
