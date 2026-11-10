"""推送执行测试：三种模式、失败回退、历史落库。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rsshub.application.services.dispatcher import Dispatcher
from rsshub.domain.entry import Entry
from rsshub.domain.settings import EffectiveSettings
from rsshub.infrastructure.persistence.models import Feed, Subscription
from rsshub.infrastructure.persistence.store import Store
from tests.support import StubCtx, make_config


def _delay_seconds(iso: str) -> float:
    return (datetime.fromisoformat(iso) - datetime.now(UTC)).total_seconds()


def _settings(mode: str) -> EffectiveSettings:
    return EffectiveSettings(
        interval_seconds=600,
        notify=True,
        push_mode=mode,
        length_limit=0,
        display_author=0,
        display_via=0,
        display_title=0,
        display_entry_tags=0,
        style=0,
        display_media=0,
    )


@pytest.fixture
def store(tmp_path: object) -> Store:
    return Store.open(tmp_path / "rsshub.db")  # type: ignore[operator]


def _fixtures(store: Store) -> tuple[Feed, Subscription, list[Entry]]:
    store.repos.users.get_or_create("u1")
    feed = store.repos.feeds.save(Feed(link="https://example.com/feed.xml", title="示例源"))
    sub = store.repos.subs.save(Subscription(user_id="u1", feed_id=feed.id, target_session="s1"))
    entries = [Entry(title="第一篇", link="https://example.com/1", summary="摘要一")]
    return feed, sub, entries


@pytest.mark.asyncio
async def test_direct_mode_sends_text_and_records_success(store: Store, tmp_path: object) -> None:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    feed, sub, entries = _fixtures(store)
    dispatcher = Dispatcher(ctx, store, make_config())

    delivered = await dispatcher.push(sub=sub, feed=feed, entries=entries, settings=_settings("direct"), stream_id="s1")

    assert delivered is True
    assert ctx.send.sent and "第一篇" in ctx.send.sent[0][1]
    history = store.repos.history.list_by_session("s1")
    assert history[0].status == "success"


@pytest.mark.asyncio
async def test_persona_failure_falls_back_to_direct(store: Store, tmp_path: object) -> None:
    ctx = StubCtx(tmp_path, reply_text="")
    feed, sub, entries = _fixtures(store)
    dispatcher = Dispatcher(ctx, store, make_config())

    await dispatcher.push(sub=sub, feed=feed, entries=entries, settings=_settings("persona"), stream_id="s1")

    assert ctx.mai_reply_calls, "应当先尝试人格化生成"
    assert ctx.send.sent, "生成失败后应回退直推"
    fallback = ctx.send.sent[-1][1]
    assert "第一篇" in fallback and "https://example.com/1" in fallback, "回退的是直推模板原文"


@pytest.mark.asyncio
async def test_persona_success_sends_generated_text(store: Store, tmp_path: object) -> None:
    ctx = StubCtx(tmp_path, reply_text="刚看到一篇挺有意思的")
    feed, sub, entries = _fixtures(store)
    dispatcher = Dispatcher(ctx, store, make_config())

    await dispatcher.push(sub=sub, feed=feed, entries=entries, settings=_settings("persona"), stream_id="s1")

    assert ctx.send.sent[-1][1] == "刚看到一篇挺有意思的"
    assert "第一篇" not in ctx.send.sent[-1][1]


@pytest.mark.asyncio
async def test_silent_mode_injects_and_triggers_thinking(store: Store, tmp_path: object) -> None:
    ctx = StubCtx(tmp_path)
    feed, sub, entries = _fixtures(store)
    dispatcher = Dispatcher(ctx, store, make_config())

    await dispatcher.push(sub=sub, feed=feed, entries=entries, settings=_settings("silent"), stream_id="s1")

    assert ctx.mai_append_calls and ctx.mai_append_calls[0]["replyable"] is False
    assert ctx.send.sent == [], "注入模式不直接往会话发消息"
    assert len(ctx.mai_proactive_calls) == 1, "注入模式每次都要请麦麦想一轮"


@pytest.mark.asyncio
async def test_failed_push_schedules_retry_and_waits_for_due_time(store: Store, tmp_path: object) -> None:
    """发送失败记一条可重发记录，并给出退避时间；没到点前调度器不会取走它。"""
    ctx = StubCtx(tmp_path, fail_text=1)  # type: ignore[arg-type]
    feed, sub, entries = _fixtures(store)
    dispatcher = Dispatcher(ctx, store, make_config())

    await dispatcher.push(sub=sub, feed=feed, entries=entries, settings=_settings("direct"), stream_id="s1")

    record = store.repos.history.list_by_session("s1")[0]
    assert record.status == "failed"
    assert record.next_retry_at is not None
    assert 55 <= _delay_seconds(record.next_retry_at) <= 65
    assert store.repos.history.list_retryable() == [], "退避期内的记录不该被取走"

    store.repos.history.update(record.id, next_retry_at=None)
    assert len(store.repos.history.list_retryable()) == 1

    ctx.send.fail_text = 1
    assert await dispatcher.retry_failed() == 0
    retried = store.repos.history.get(record.id)
    assert retried.retry_count == 1
    assert retried.next_retry_at is not None
    assert 115 <= _delay_seconds(retried.next_retry_at) <= 125, "第二次退避翻倍"
