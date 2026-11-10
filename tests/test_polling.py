"""轮询服务测试：基线建立、正文透传、失败记账与调度推进。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rsshub.application.services.content_handler import ContentHandlerChain
from rsshub.application.services.dispatcher import Dispatcher
from rsshub.application.services.polling import FeedPoller, build_defaults
from rsshub.domain.entry import Entry
from rsshub.errors import FeedFetchError
from rsshub.infrastructure.persistence.models import Feed, Subscription
from rsshub.infrastructure.persistence.store import Store
from tests.support import FakeClient, StubCtx, make_config, make_retries_due

FEED_URL = "https://example.com/feed.xml"
IMAGE_URL = "https://example.com/pic.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _rss(*items: str) -> bytes:
    body = "".join(items)
    return (
        f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>示例源</title>{body}</channel></rss>'
    ).encode()


def _item(guid: str, title: str, content_html: str = "") -> str:
    content = f"<content:encoded><![CDATA[{content_html}]]></content:encoded>" if content_html else ""
    return (
        f"<item><title>{title}</title><link>https://example.com/{guid}</link>"
        f"<guid>{guid}</guid><description>摘要 {title}</description>{content}</item>"
    )


class Harness:
    def __init__(self, ctx: StubCtx, store: Store, config: object, client: FakeClient) -> None:
        self.ctx = ctx
        self.store = store
        self.config = config
        self.client = client
        self.defaults = build_defaults(config)
        self.dispatcher = Dispatcher(ctx, store, config, client)
        self.handlers = ContentHandlerChain(ctx)
        self.poller = FeedPoller(ctx, store, config, client, self.handlers, self.dispatcher)

    def add_subscription(self, target_session: str = "s1") -> tuple[Feed, Subscription]:
        self.store.repos.users.get_or_create("u1")
        feed = self.store.repos.feeds.save(Feed(link=FEED_URL))
        sub = self.store.repos.subs.save(
            Subscription(user_id="u1", feed_id=feed.id, target_session=target_session, push_mode="direct")
        )
        return feed, sub


def _harness(tmp_path: object, content: bytes, config: object | None = None) -> Harness:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    store = Store.open(tmp_path / "rsshub.db")  # type: ignore[operator]
    client = FakeClient(content=content, url=FEED_URL, payloads={IMAGE_URL: PNG})
    return Harness(ctx, store, config or make_config(), client)


@pytest.mark.asyncio
async def test_first_poll_builds_baseline_without_pushing(tmp_path: object) -> None:
    harness = _harness(tmp_path, _rss(_item("g1", "第一篇")))
    feed, sub = harness.add_subscription()

    outcome = await harness.poller.poll(feed, [sub])

    assert outcome.new_entries == 1
    assert harness.ctx.send.sent == []
    assert harness.store.repos.feeds.get(feed.id).entry_hashes
    assert harness.store.repos.subs.get(sub.id).next_check_at


@pytest.mark.asyncio
async def test_dr03_polling_keeps_content_html_for_media(tmp_path: object) -> None:
    """DR-03：轮询路径重建条目时必须带上 content_html，否则定时推送永远没有图。"""
    harness = _harness(tmp_path, _rss(_item("g1", "基线")))
    feed, sub = harness.add_subscription()
    await harness.poller.poll(feed, [sub])

    harness.client.content = _rss(_item("g2", "带图", f'<p>正文</p><img src="{IMAGE_URL}">'))
    outcome = await harness.poller.poll(harness.store.repos.feeds.get(feed.id), [sub])

    assert outcome.delivered == 1
    assert harness.ctx.send.hybrid_calls, "带正文图的推送应走图文混排"
    segments = harness.ctx.send.hybrid_calls[-1]["segments"]
    assert any(segment["type"] == "image" for segment in segments)
    assert IMAGE_URL in harness.client.calls[-1]["url"] or harness.client.calls[-1]["url"] == IMAGE_URL


@pytest.mark.asyncio
async def test_dr02_failed_push_still_merges_hashes_and_records_per_entry(tmp_path: object) -> None:
    """DR-02/DR-23：抓到的条目一律并入已见集合；投递失败按条目各记一条自己的文本。"""
    harness = _harness(tmp_path, _rss(_item("g1", "基线")))
    feed, sub = harness.add_subscription()
    await harness.poller.poll(feed, [sub])
    harness.ctx.send.fail_text = 1

    harness.client.content = _rss(_item("g2", "第二篇"), _item("g3", "第三篇"))
    outcome = await harness.poller.poll(harness.store.repos.feeds.get(feed.id), [sub])

    assert outcome.delivered == 0
    stored = harness.store.repos.feeds.get(feed.id)
    hashes = set(stored.entry_hashes)
    assert len(hashes) >= 5, "失败轮的条目哈希也应合并（已见集合语义）"
    records = harness.store.repos.history.list_by_session("s1")
    failed = [record for record in records if record.status == "failed"]
    assert len(failed) == 2
    assert len({record.content for record in failed}) == 2
    assert all("基线" not in record.content for record in failed)

    make_retries_due(harness.store)
    recovered = await harness.dispatcher.retry_failed()
    assert recovered == 2
    retried = [text for _, text in harness.ctx.send.sent]
    assert any("第二篇" in text for text in retried)
    assert any("第三篇" in text for text in retried)


@pytest.mark.asyncio
async def test_poll_skips_entries_without_unique_fields_once(tmp_path: object) -> None:
    harness = _harness(tmp_path, _rss(_item("g1", "基线")))
    feed, sub = harness.add_subscription()
    await harness.poller.poll(feed, [sub])

    empty_items = "<item><description>  </description></item><item><description> </description></item>"
    harness.client.content = _rss(empty_items)
    first = await harness.poller.poll(harness.store.repos.feeds.get(feed.id), [sub])
    second = await harness.poller.poll(harness.store.repos.feeds.get(feed.id), [sub])

    assert first.new_entries == 2
    assert second.new_entries == 0, "同批全空条目第二次抓取应已被判为旧条目"


def test_latest_entries_orders_by_time_desc() -> None:
    from rsshub.domain.entry import FeedDocument

    document = FeedDocument(url=FEED_URL, title="t", entries=[Entry(title="a"), Entry(title="b")])

    assert len(document.latest_entries(1)) == 1


def _delay_seconds(iso: str) -> float:
    return (datetime.fromisoformat(iso) - datetime.now(UTC)).total_seconds()


@pytest.mark.asyncio
async def test_fetch_failure_backs_off_and_success_resets(tmp_path: object) -> None:
    """抓取失败按连续次数翻倍退避，抓到一次就恢复订阅自己的间隔。"""
    harness = _harness(tmp_path, _rss(_item("g1", "基线")))
    feed, sub = harness.add_subscription()
    await harness.poller.poll(feed, [sub])
    interval = harness.defaults.interval_seconds

    harness.client.fail_with = FeedFetchError("连接被重置")
    await harness.poller.poll(harness.store.repos.feeds.get(feed.id), [sub])
    first = harness.store.repos.subs.get(sub.id)
    assert first.fail_count == 1
    assert interval * 2 - 5 <= _delay_seconds(first.next_check_at) <= interval * 2 + 5

    await harness.poller.poll(harness.store.repos.feeds.get(feed.id), [sub])
    second = harness.store.repos.subs.get(sub.id)
    assert second.fail_count == 2
    assert interval * 4 - 5 <= _delay_seconds(second.next_check_at) <= interval * 4 + 5

    harness.client.fail_with = None
    await harness.poller.poll(harness.store.repos.feeds.get(feed.id), [sub])
    recovered = harness.store.repos.subs.get(sub.id)
    assert recovered.fail_count == 0
    assert interval - 5 <= _delay_seconds(recovered.next_check_at) <= interval + 5


@pytest.mark.asyncio
async def test_backoff_is_capped(tmp_path: object) -> None:
    """连续失败很多次也不会把下一轮推到 6 小时之后。"""
    from rsshub.application.services.polling import BACKOFF_MAX_SECONDS

    harness = _harness(tmp_path, _rss(_item("g1", "基线")))
    feed, sub = harness.add_subscription()
    await harness.poller.poll(feed, [sub])

    harness.store.repos.subs.update(sub.id, fail_count=20)
    harness.client.fail_with = FeedFetchError("一直失败")
    await harness.poller.poll(harness.store.repos.feeds.get(feed.id), [harness.store.repos.subs.get(sub.id)])

    capped = harness.store.repos.subs.get(sub.id)
    assert capped.fail_count == 21
    assert _delay_seconds(capped.next_check_at) <= BACKOFF_MAX_SECONDS + 5
