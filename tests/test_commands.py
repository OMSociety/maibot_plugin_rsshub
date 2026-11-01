"""订阅服务与命令面测试。"""

from __future__ import annotations

import pytest

from rsshub.application.commands.rss_commands import RssCommandsMixin
from rsshub.application.services.subscription_service import SubscriptionService
from rsshub.infrastructure.persistence.store import Store
from tests.support import FakeClient, StubCtx, make_config

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>示例源</title><link>https://example.com</link>
  <item><title>第一篇</title><link>https://example.com/1</link><guid>p1</guid></item>
</channel></rss>"""
RSS = RSS_XML.encode("utf-8")


class Harness(RssCommandsMixin):
    def __init__(self, ctx: StubCtx, store: Store, config: object, client: FakeClient) -> None:
        from rsshub.application.services.content_handler import ContentHandlerChain
        from rsshub.application.services.dispatcher import Dispatcher
        from rsshub.application.services.polling import build_defaults

        self.ctx = ctx
        self.store = store
        self.config = config
        self.defaults = build_defaults(config)
        self.sub_service = SubscriptionService(ctx, store, config, client)
        self.handler_chain = ContentHandlerChain(ctx)
        self._client = client
        self._dispatcher = Dispatcher(ctx, store, config, client)


@pytest.fixture
def harness(tmp_path: object) -> Harness:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    store = Store.open(tmp_path / "rsshub.db")  # type: ignore[operator]
    config = make_config()
    client = FakeClient(content=RSS, url="https://example.com/feed.xml")
    return Harness(ctx, store, config, client)


@pytest.mark.asyncio
async def test_sub_command_creates_subscription(harness: Harness) -> None:
    result = await harness.handle_sub(
        stream_id="s1", matched_groups={"link": "https://example.com/feed.xml"}, user_id="u1"
    )

    assert result[0] is True
    assert "示例源" in harness.ctx.send.sent[-1][1]
    subs = harness.store.repos.subs.list_by_session("s1")
    assert len(subs) == 1
    assert subs[0].title == "示例源"


@pytest.mark.asyncio
async def test_sub_command_rejects_duplicate(harness: Harness) -> None:
    await harness.handle_sub(stream_id="s1", matched_groups={"link": "https://example.com/feed.xml"}, user_id="u1")
    await harness.handle_sub(stream_id="s1", matched_groups={"link": "https://example.com/feed.xml"}, user_id="u1")

    assert "已经订阅过" in harness.ctx.send.sent[-1][1]
    assert len(harness.store.repos.subs.list_by_session("s1")) == 1


@pytest.mark.asyncio
async def test_sub_command_rejects_private_address_without_permission(harness: Harness) -> None:
    config = make_config(
        network=type("N", (), {"proxy_enabled": False, "proxy_url": "", "allow_private_addresses": False})()
    )
    harness.sub_service = SubscriptionService(harness.ctx, harness.store, config, FakeClient(content=RSS))

    result = await harness.handle_sub(
        stream_id="s1", matched_groups={"link": "http://127.0.0.1/feed.xml"}, user_id="u1"
    )

    assert result[0] is True
    assert "订阅失败" in harness.ctx.send.sent[-1][1]


@pytest.mark.asyncio
async def test_list_and_unsub_commands(harness: Harness) -> None:
    await harness.handle_sub(stream_id="s1", matched_groups={"link": "https://example.com/feed.xml"}, user_id="u1")

    await harness.handle_list(stream_id="s1")
    assert "示例源" in harness.ctx.send.sent[-1][1]

    await harness.handle_unsub(stream_id="s1", matched_groups={}, user_id="u1")
    assert "已取消 1 个订阅" in harness.ctx.send.sent[-1][1]
    assert harness.store.repos.subs.list_by_session("s1") == []


@pytest.mark.asyncio
async def test_set_option_and_session_defaults(harness: Harness) -> None:
    sub, _ = await harness.sub_service.subscribe(user_id="u1", raw_link="https://example.com/feed.xml", stream_id="s1")

    updated = harness.sub_service.set_subscription_option("u1", sub.id, "interval", "15")

    assert updated.interval_minutes == 15
    harness.sub_service.set_session_defaults("s1", {"push_mode": "direct"})
    assert harness.sub_service.get_session_defaults("s1")["push_mode"] == "direct"


@pytest.mark.asyncio
async def test_test_push_sends_latest_entries_without_history(harness: Harness) -> None:
    await harness.handle_sub(stream_id="s1", matched_groups={"link": "https://example.com/feed.xml"}, user_id="u1")
    sub = harness.store.repos.subs.list_by_session("s1")[0]
    harness.sub_service.set_subscription_option("u1", sub.id, "push_mode", "direct")
    before = len(harness.ctx.send.sent)

    await harness.handle_test_push(stream_id="s1", is_local_operator=True)

    pushed = harness.ctx.send.sent[before:]
    assert any("第一篇" in text for _, text in pushed), pushed
    assert harness.store.repos.history.list_by_session("s1") == []


def test_test_push_declares_operator_permission() -> None:
    info = RssCommandsMixin.handle_test_push.__maibot_component_info__

    assert info.metadata.get("permission") == "operator"


@pytest.mark.asyncio
async def test_test_push_needs_id_with_multiple_subs(harness: Harness) -> None:
    await harness.handle_sub(stream_id="s1", matched_groups={"link": "https://example.com/feed.xml"}, user_id="u1")
    await harness.sub_service.subscribe(
        user_id="u1", raw_link="https://example.com/other.xml", stream_id="s1", title="第二个"
    )

    await harness.handle_test_push(stream_id="s1", is_local_operator=True)

    assert "请带编号" in harness.ctx.send.sent[-1][1]


def test_command_ids_and_bilingual_patterns() -> None:
    expected = {
        "handle_sub": ("rss_sub", ("sub", "订阅")),
        "handle_unsub": ("rss_unsub", ("unsub", "退订")),
        "handle_list": ("rss_list", ("sub_list", "列表")),
        "handle_status": ("rss_status", ("sub_status", "状态")),
        "handle_help": ("rss_help", ("rsshelp", "帮助")),
        "handle_test_push": ("rss_test", ("sub_test", "测试推送")),
    }

    for attr, (name, words) in expected.items():
        info = getattr(RssCommandsMixin, attr).__maibot_component_info__
        assert info.name == name
        for word in words:
            assert word in info.command_pattern, (name, word)
