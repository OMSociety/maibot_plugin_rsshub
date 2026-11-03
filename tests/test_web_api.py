"""WebUI 静态 API 测试。"""

from __future__ import annotations

import pytest

from rsshub.application.services.subscription_service import SubscriptionService
from rsshub.infrastructure.persistence.store import Store
from rsshub.interfaces.web_api import WebApiMixin
from tests.support import FakeClient, StubCtx, make_config

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>示例源</title><link>https://example.com</link>
<item><title>第一篇</title><link>https://example.com/1</link><guid>p1</guid></item>
</channel></rss>"""


class Harness(WebApiMixin):
    def __init__(self, ctx: StubCtx, store: Store, client: FakeClient) -> None:
        from rsshub.application.services.polling import build_defaults

        self.ctx = ctx
        self.store = store
        self.config = make_config()
        self.defaults = build_defaults(self.config)
        self.sub_service = SubscriptionService(ctx, store, self.config, client)
        self._poller = None


@pytest.fixture
def harness(tmp_path: object) -> Harness:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    store = Store.open(tmp_path / "rsshub.db")  # type: ignore[operator]
    return Harness(ctx, store, FakeClient(content=RSS_XML.encode("utf-8")))


@pytest.mark.asyncio
async def test_overview_reports_counts(harness: Harness) -> None:
    from rsshub.infrastructure.persistence.models import PushHistory

    await harness.sub_service.subscribe(user_id="u1", raw_link="https://example.com/feed.xml", stream_id="s1")
    harness.store.repos.history.add(
        PushHistory(user_id="u1", content="x", target_session="s1", status="success", source_type="agent")
    )

    data = await harness.webui_overview()

    assert data["ready"] is True
    assert data["subscriptions"] == 1
    assert data["feeds"] == 1
    assert data["sessions"] == 1
    assert data["poll_interval_minutes"] == 10
    assert data["pushes_24h"] == 1
    assert data["failures_24h"] == 0


@pytest.mark.asyncio
async def test_overview_counts_failures_and_pending_retries(harness: Harness) -> None:
    from rsshub.infrastructure.persistence.models import PushHistory

    await harness.sub_service.subscribe(user_id="u1", raw_link="https://example.com/feed.xml", stream_id="s1")
    rows = (
        ("success", 0, 3),
        ("failed", 0, 3),
        ("failed", 3, 3),
        ("stopped", 0, 3),
    )
    for status, retry_count, max_retries in rows:
        harness.store.repos.history.add(
            PushHistory(
                user_id="u1",
                content="x",
                target_session="s1",
                status=status,
                source_type="agent",
                retry_count=retry_count,
                max_retries=max_retries,
            )
        )

    data = await harness.webui_overview()

    assert data["pushes_24h"] == 4, "统计口径是 24 小时内的全部投递记录"
    assert data["failures_24h"] == 3, "failed 与 stopped 都算没送达，success 不算"
    assert data["pending_retry"] == 1, "待重试只数可以再试的失败记录"


@pytest.mark.asyncio
async def test_list_subscriptions_supports_keyword(harness: Harness) -> None:
    await harness.sub_service.subscribe(user_id="u1", raw_link="https://example.com/feed.xml", stream_id="s1")

    hit = await harness.webui_list_subscriptions(keyword="示例")
    miss = await harness.webui_list_subscriptions(keyword="不存在的关键字")

    assert hit["total"] == 1 and hit["items"][0]["title"] == "示例源"
    assert miss["total"] == 0


@pytest.mark.asyncio
async def test_update_subscription_and_state(harness: Harness) -> None:
    sub = (
        await harness.sub_service.subscribe(
            user_id="u1",
            raw_link="https://example.com/feed.xml",
            stream_id="s1",
            platform="qq",
            group_id="123456789",
        )
    ).sub

    updated = await harness.webui_update_subscription(sub_id=sub.id, key="interval", value="30")
    assert updated["value"] == 30

    reset = await harness.webui_update_subscription(sub_id=sub.id, key="interval", value="0")
    assert reset["value"] == -100, "清空间隔回到继承值，解析时向上取全局默认"
    from rsshub.domain.settings import resolve_settings

    refreshed = harness.store.repos.subs.get(sub.id)
    assert resolve_settings(refreshed, None, harness.defaults, None).interval_seconds == 600

    await harness.webui_set_subscription_state(sub_id=sub.id, enabled=False)
    assert harness.store.repos.subs.get(sub.id).state == 0

    listed = await harness.webui_list_subscriptions()
    assert listed["total"] == 1
    assert listed["items"][0]["state"] == 0
    assert listed["items"][0]["session_label"] == "QQ群:123456789"

    overview = await harness.webui_overview()
    assert overview["subscriptions"] == 0
    assert overview["known_sessions"] == [{"session": "s1", "label": "QQ群:123456789"}]


@pytest.mark.asyncio
async def test_delete_subscription(harness: Harness) -> None:
    sub = (
        await harness.sub_service.subscribe(user_id="u1", raw_link="https://example.com/feed.xml", stream_id="s1")
    ).sub

    await harness.webui_delete_subscription(sub_id=sub.id)

    assert harness.store.repos.subs.get(sub.id) is None


@pytest.mark.asyncio
async def test_unknown_option_rejected(harness: Harness) -> None:
    sub = (
        await harness.sub_service.subscribe(user_id="u1", raw_link="https://example.com/feed.xml", stream_id="s1")
    ).sub

    with pytest.raises(ValueError):
        await harness.webui_update_subscription(sub_id=sub.id, key="nonsense", value="1")


@pytest.mark.asyncio
async def test_history_and_import_preserve_session_label(harness: Harness) -> None:
    from rsshub.infrastructure.persistence.models import PushHistory

    await harness.sub_service.subscribe(
        user_id="u1",
        raw_link="https://example.com/feed.xml",
        stream_id="s1",
        platform="qq",
        group_id="123456789",
    )
    harness.store.repos.history.add(
        PushHistory(user_id="u1", content="x", target_session="s1", status="success", source_type="feed")
    )

    history = await harness.webui_list_history()
    assert history["items"][0]["session_label"] == "QQ群:123456789"

    res = await harness.webui_import_subscriptions(links="https://example.com/other.xml", session="s1")
    assert res["imported"] == 1
    subs = harness.store.repos.subs.list_by_session("s1")
    imported_sub = next(s for s in subs if s.user_id == "webui")
    assert imported_sub.session_label == "QQ群:123456789"
    assert imported_sub.platform_name == "qq"
