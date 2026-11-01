"""数据库迁移与会话显示名。"""

from __future__ import annotations

import sqlite3

import pytest

from rsshub.application.services.subscription_service import SubscriptionService, _session_label
from rsshub.infrastructure.persistence.database import _DDL_V1, Database
from rsshub.infrastructure.persistence.store import Store
from tests.support import FakeClient, StubCtx, make_config

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>G</title><link>https://e.com</link>
<item><title>T</title><link>https://e.com/1</link><guid>g1</guid></item></channel></rss>"""


def test_session_label_prefers_group_then_private() -> None:
    assert _session_label("qq", "945523689", "1024", "hash") == "QQ群:1024"
    assert _session_label("qq", "945523689", "", "hash") == "QQ私聊:945523689"
    assert _session_label("", "945523689", "", "hash") == "hash"


def test_migration_upgrades_v1_database(tmp_path: object) -> None:
    path = tmp_path / "old.db"  # type: ignore[operator]
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE rsshub_schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL,"
        "description TEXT NOT NULL DEFAULT '')"
    )
    conn.executescript(_DDL_V1)
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

    sub, _ = await service.subscribe(
        user_id="u1",
        raw_link="https://e.com/feed.xml",
        stream_id="hash-session",
        platform="qq",
        group_id="1024",
    )

    assert sub.target_session == "hash-session"
    assert sub.session_label == "QQ群:1024"
    assert store.repos.subs.get(sub.id).session_label == "QQ群:1024"
