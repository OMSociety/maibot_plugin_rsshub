"""建库与订阅的会话显示名。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rsshub.application.services.subscription_service import SubscriptionService, _session_label
from rsshub.infrastructure.persistence.store import Store
from tests.support import FakeClient, StubCtx, make_config

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>G</title><link>https://e.com</link>
<item><title>T</title><link>https://e.com/1</link><guid>g1</guid></item></channel></rss>"""


def test_schema_covers_retry_and_backoff_columns(tmp_path: Path) -> None:
    """建表脚本本身就是最终结构：订阅表有失败计数，历史表有重试时间。"""
    store = Store.open(tmp_path / "rsshub.db")
    try:
        sub_columns = {row[1] for row in store.db.conn.execute("PRAGMA table_info(rsshub_subs)")}
        history_columns = {row[1] for row in store.db.conn.execute("PRAGMA table_info(rsshub_push_history)")}
    finally:
        store.close()

    assert {"session_label", "fail_count", "next_check_at"} <= sub_columns
    assert {"retry_count", "max_retries", "next_retry_at"} <= history_columns


def test_store_reopen_keeps_data(tmp_path: Path) -> None:
    """建表幂等：重复打开同一个库不会清数据。"""
    path = tmp_path / "rsshub.db"
    store = Store.open(path)
    store.repos.users.get_or_create("u1")
    store.close()

    store = Store.open(path)
    try:
        assert store.repos.users.get("u1") is not None
    finally:
        store.close()


def test_session_label_prefers_group_then_private() -> None:
    assert _session_label("qq", "123456789", "1024", "hash") == "QQ群:1024"
    assert _session_label("qq", "123456789", "", "hash") == "QQ私聊:123456789"
    assert _session_label("", "123456789", "", "hash") == "hash"


@pytest.mark.asyncio
async def test_subscribe_records_readable_session(tmp_path: Path) -> None:
    ctx = StubCtx(tmp_path)
    store = Store.open(tmp_path / "rsshub.db")
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
    store.close()


def _write_legacy_db(path: Path) -> None:
    """造一个旧版库：订阅表没有 fail_count，历史表没有 next_retry_at。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE rsshub_subs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            state INTEGER NOT NULL DEFAULT 1,
            user_id TEXT NOT NULL,
            feed_id INTEGER NOT NULL,
            target_session TEXT,
            next_check_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE rsshub_push_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            status TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 3,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO rsshub_subs (user_id, feed_id, target_session, created_at, updated_at) "
        "VALUES ('u1', 7, 'hash', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()


def test_open_adds_missing_columns_and_keeps_rows(tmp_path: Path) -> None:
    """老库升级：缺的列按建表脚本补上，已有行与数据都在，补完能按新列读写。"""
    path = tmp_path / "rsshub.db"
    _write_legacy_db(path)

    store = Store.open(path)
    try:
        assert "rsshub_subs.fail_count" in store.added_columns
        assert "rsshub_push_history.next_retry_at" in store.added_columns

        sub = store.repos.subs.get(1)
        assert sub is not None and sub.target_session == "hash"
        assert sub.fail_count == 0
        store.repos.subs.update(1, fail_count=3)
        assert store.repos.subs.get(1).fail_count == 3

        store.db.execute(
            "INSERT INTO rsshub_push_history (user_id, status, created_at, updated_at) VALUES (?,?,?,?)",
            ("u1", "failed", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        assert store.repos.history.list_retryable() != []
    finally:
        store.close()

    store = Store.open(path)
    try:
        assert store.added_columns == [], "补过一次的库再打开不该重复补列"
    finally:
        store.close()
