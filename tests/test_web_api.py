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
    await harness.sub_service.subscribe(user_id="u1", raw_link="https://example.com/feed.xml", stream_id="s1")

    data = await harness.webui_overview()

    assert data["ready"] is True
    assert data["subscriptions"] == 1
    assert data["feeds"] == 1
    assert data["sessions"] == 1
    assert data["poll_interval_minutes"] == 10


@pytest.mark.asyncio
async def test_list_subscriptions_supports_keyword(harness: Harness) -> None:
    await harness.sub_service.subscribe(user_id="u1", raw_link="https://example.com/feed.xml", stream_id="s1")

    hit = await harness.webui_list_subscriptions(keyword="示例")
    miss = await harness.webui_list_subscriptions(keyword="不存在的关键字")

    assert hit["total"] == 1 and hit["items"][0]["title"] == "示例源"
    assert miss["total"] == 0


@pytest.mark.asyncio
async def test_update_subscription_and_state(harness: Harness) -> None:
    sub, _ = await harness.sub_service.subscribe(user_id="u1", raw_link="https://example.com/feed.xml", stream_id="s1")

    updated = await harness.webui_update_subscription(sub_id=sub.id, key="interval", value="30")
    assert updated["value"] == 30

    await harness.webui_set_subscription_state(sub_id=sub.id, enabled=False)
    assert harness.store.repos.subs.get(sub.id).state == 0


@pytest.mark.asyncio
async def test_delete_subscription(harness: Harness) -> None:
    sub, _ = await harness.sub_service.subscribe(user_id="u1", raw_link="https://example.com/feed.xml", stream_id="s1")

    await harness.webui_delete_subscription(sub_id=sub.id)

    assert harness.store.repos.subs.get(sub.id) is None


@pytest.mark.asyncio
async def test_unknown_option_rejected(harness: Harness) -> None:
    sub, _ = await harness.sub_service.subscribe(user_id="u1", raw_link="https://example.com/feed.xml", stream_id="s1")

    with pytest.raises(ValueError):
        await harness.webui_update_subscription(sub_id=sub.id, key="nonsense", value="1")
