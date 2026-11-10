"""调度器测试：轮询组的异常必须留下日志。"""

from __future__ import annotations

from typing import Any

import pytest

from rsshub.application.services.dispatcher import Dispatcher
from rsshub.application.services.polling import PollOutcome
from rsshub.application.services.scheduler import Scheduler
from rsshub.infrastructure.persistence.models import Feed, Subscription
from rsshub.infrastructure.persistence.store import Store
from tests.support import FakeClient, StubCtx, make_config


class _BoomPoller:
    async def poll(self, feed: Feed, subs: list[Subscription]) -> PollOutcome:
        raise RuntimeError("组内异常")


def _due_subscription(store: Store) -> tuple[Feed, Subscription]:
    store.repos.users.get_or_create("u1")
    feed = store.repos.feeds.save(Feed(link="https://example.com/feed.xml"))
    sub = store.repos.subs.save(Subscription(user_id="u1", feed_id=feed.id, target_session="s1"))
    return feed, sub


@pytest.mark.asyncio
async def test_tick_logs_group_exception(tmp_path: Any) -> None:
    """gather 收了异常也要记下来，否则组内报错在日志里完全看不见。"""
    ctx = StubCtx(tmp_path)
    store = Store.open(tmp_path / "rsshub.db")
    _due_subscription(store)
    dispatcher = Dispatcher(ctx, store, make_config(), FakeClient())
    scheduler = Scheduler(ctx, store, make_config(), _BoomPoller(), dispatcher)

    try:
        await scheduler.tick()
    finally:
        store.close()

    errors = [message for level, message in ctx.logger.messages if level == "error"]
    assert any("RSS 轮询组异常退出" in message for message in errors)
    assert any("组内异常" in message for message in errors)
