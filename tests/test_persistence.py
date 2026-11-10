"""建库与订阅的会话显示名。"""

from __future__ import annotations

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
