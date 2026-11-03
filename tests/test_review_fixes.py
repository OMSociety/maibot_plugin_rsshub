"""审查缺陷的回归测试（DR 编号对应 review-rsshub-20261103.md §4）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest

from rsshub.application.services.dispatcher import Dispatcher
from rsshub.application.services.message_format import build_entry_text
from rsshub.domain.entry import Entry
from rsshub.domain.settings import EffectiveSettings, GlobalDefaults, resolve_settings
from rsshub.infrastructure.fetcher.dedup import entry_hash_set, is_known_entry
from rsshub.infrastructure.fetcher.parser import parse_feed
from rsshub.infrastructure.media.downloader import MediaDownloader
from rsshub.infrastructure.persistence.models import Feed, PushHistory, Subscription
from rsshub.infrastructure.persistence.store import Store
from tests.support import FakeClient, StubCtx, make_config

RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>a</title>
<item><title>b</title><link>https://example.com/1</link><guid>g1</guid></item>
</channel></rss>"""


def _settings(mode: str = "direct", **overrides: object) -> EffectiveSettings:
    values: dict = {
        "interval_seconds": 600,
        "notify": True,
        "push_mode": mode,
        "length_limit": 0,
        "display_author": 0,
        "display_via": 0,
        "display_title": 0,
        "display_entry_tags": 0,
        "style": 0,
        "display_media": 0,
    }
    values.update(overrides)
    return EffectiveSettings(**values)


@pytest.fixture
def store(tmp_path: object) -> Store:
    return Store.open(tmp_path / "rsshub.db")  # type: ignore[operator]


def _fixtures(store: Store, count: int = 3) -> tuple[Feed, Subscription, list[Entry]]:
    store.repos.users.get_or_create("u1")
    feed = store.repos.feeds.save(Feed(link="https://example.com/feed.xml", title="示例源"))
    sub = store.repos.subs.save(Subscription(user_id="u1", feed_id=feed.id, target_session="s1"))
    entries = [
        Entry(title=f"第{i}篇", link=f"https://example.com/{i}", summary=f"摘要{i}", guid=f"g{i}")
        for i in range(1, count + 1)
    ]
    return feed, sub, entries


@pytest.mark.asyncio
async def test_dr01_retry_resends_each_entry_text_once(store: Store, tmp_path: object) -> None:
    """DR-01：整批失败后重试必须按条目各发一次自己的文本，而不是整批文本发 N 遍。"""
    ctx = StubCtx(tmp_path, fail_text=1)  # 第一次发送失败
    feed, sub, entries = _fixtures(store)
    dispatcher = Dispatcher(ctx, store, make_config())

    delivered = await dispatcher.push(sub=sub, feed=feed, entries=entries, settings=_settings(), stream_id="s1")

    assert delivered is False
    failed = store.repos.history.list_by_session("s1")
    assert len(failed) == 3
    texts = {record.content for record in failed}
    assert len(texts) == 3, "每条记录应各存自己的文本"
    assert all("第1篇" in record.content or "第2篇" in record.content or "第3篇" in record.content for record in failed)

    recovered = await dispatcher.retry_failed()
    assert recovered == 3
    sent = [text for _, text in ctx.send.sent]
    assert len(sent) == 3
    for index, text in enumerate(sent, start=1):
        assert text.count("【示例源】更新") == 1
        assert sum(f"第{i}篇" in text for i in range(1, 4)) == 1, f"第 {index} 条重试文本混入了别的条目：{text}"


@pytest.mark.asyncio
async def test_dr01_retry_skips_when_entry_already_delivered(store: Store, tmp_path: object) -> None:
    """DR-01 幂等：同一条目已有成功记录时不再重复发送。"""
    ctx = StubCtx(tmp_path)
    feed, sub, entries = _fixtures(store, count=1)
    dispatcher = Dispatcher(ctx, store, make_config())

    await dispatcher.push(sub=sub, feed=feed, entries=entries, settings=_settings(), stream_id="s1")
    again = await dispatcher.push(sub=sub, feed=feed, entries=entries, settings=_settings(), stream_id="s1")

    assert again is True
    assert len(ctx.send.sent) == 1


@pytest.mark.asyncio
async def test_dr25_retry_stops_for_deleted_subscription(store: Store, tmp_path: object) -> None:
    ctx = StubCtx(tmp_path, fail_text=1)
    feed, sub, entries = _fixtures(store, count=1)
    dispatcher = Dispatcher(ctx, store, make_config())
    await dispatcher.push(sub=sub, feed=feed, entries=entries, settings=_settings(), stream_id="s1")

    store.repos.subs.delete(sub.id)
    recovered = await dispatcher.retry_failed()

    assert recovered == 0
    record = store.repos.history.list_by_session("s1")[0]
    assert record.status == "stopped"
    assert record.fail_reason == "订阅已删除"


@pytest.mark.asyncio
async def test_dr11_max_images_zero_keeps_av_links_and_drops_images(store: Store, tmp_path: object) -> None:
    """DR-11/12：max_images=0 表示完全不带图，音视频链接行仍要给出。"""
    ctx = StubCtx(tmp_path)
    feed, sub, _ = _fixtures(store, count=1)
    entry = Entry(
        title="带媒体",
        link="https://example.com/post",
        summary="摘要",
        content_html='<img src="https://example.com/a.png"><video src="https://example.com/v.mp4"></video>',
    )
    client = FakeClient(payloads={"https://example.com/a.png": b"\x89PNG\r\n\x1a\n" + b"0" * 32})
    dispatcher = Dispatcher(
        ctx,
        store,
        make_config(
            media=SimpleNamespace(
                max_images=0,
                image_max_mib=1,
                table_render_enabled=True,
                multi_image_forward=True,
                attach_files=True,
                max_files=2,
                file_max_mib=20,
            )
        ),
        client,
    )

    await dispatcher.push(sub=sub, feed=feed, entries=[entry], settings=_settings(), stream_id="s1")

    text = ctx.send.sent[-1][1]
    assert "音视频：https://example.com/v.mp4" in text
    assert ctx.send.hybrid_calls == []
    assert client.calls == [], "不带图时不应发起任何图片下载"


@pytest.mark.asyncio
async def test_dr12_over_limit_images_count_as_skipped(tmp_path: object) -> None:
    client = FakeClient(payloads={f"https://example.com/{i}.png": b"\x89PNG\r\n\x1a\n" + b"0" * 32 for i in range(3)})
    downloader = MediaDownloader(client, max_images=1, max_bytes=1024 * 1024)
    entry = Entry(
        title="多图",
        link="https://example.com/post",
        content_html="".join(f'<img src="https://example.com/{i}.png">' for i in range(3)),
    )

    bundle = await downloader.collect([entry])

    assert len(bundle.images) == 1
    assert bundle.skipped == 1, "下载成功但被张数上限丢掉的图也应计入 skipped"


def test_dr14_empty_entries_get_distinct_hashes() -> None:
    first = Entry()
    second = Entry()

    assert entry_hash_set(first, 0) != entry_hash_set(second, 1)
    known = set(entry_hash_set(first, 0))
    assert is_known_entry(first, known, 0)
    assert not is_known_entry(second, known, 1)


def test_dr05_display_via_and_style_are_not_advertised() -> None:
    from rsshub.application.services.subscription_service import SUB_OPTION_KEYS
    from rsshub.application.tools.rss_tools import _OPTION_HELP

    assert "display_via" not in SUB_OPTION_KEYS
    assert "style" not in SUB_OPTION_KEYS
    assert "display_via" not in _OPTION_HELP
    assert "style" not in _OPTION_HELP
    assert "0 自动" in _OPTION_HELP


def test_dr06_session_defaults_participate_in_resolution() -> None:
    defaults = GlobalDefaults()
    sub = Subscription(user_id="u1", feed_id=1, target_session="s1")
    session = {"push_mode": "silent", "display_media": 2}

    settings = resolve_settings(sub, None, defaults, session)

    assert settings.push_mode == "silent"
    assert settings.display_media == 2

    sub.push_mode = "direct"
    assert resolve_settings(sub, None, defaults, session).push_mode == "direct"


def test_dr07_json_feed_is_parsed() -> None:
    body = (
        b'{"version":"https://jsonfeed.org/version/1.1","title":"JSON \xe6\xba\x90","items":['
        b'{"id":"1","url":"https://example.com/a","title":"\xe6\xa0\x87\xe9\xa2\x98",'
        b'"content_html":"<p>\xe6\xad\xa3\xe6\x96\x87</p>","date_published":"2026-01-02T03:04:05Z",'
        b'"attachments":[{"url":"https://example.com/a.mp3","mime_type":"audio/mpeg","size_in_bytes":12}]}]}'
    )

    document = parse_feed(body, "https://example.com/feed.json")

    assert document.title == "JSON 源"
    assert len(document.entries) == 1
    assert document.entries[0].content_html == "<p>正文</p>"
    assert document.entries[0].enclosures[0].url == "https://example.com/a.mp3"


def test_dr16_interval_bounds(store: Store) -> None:
    from rsshub.application.services.subscription_service import _coerce

    with pytest.raises(ValueError):
        _coerce("interval", "0")
    with pytest.raises(ValueError):
        _coerce("interval", "1441")
    assert _coerce("interval", "15") == 15


def test_dr17_string_boolean_false_denies() -> None:
    from rsshub.application.services.content_handler import _as_bool

    assert _as_bool("false") is False
    assert _as_bool("0") is False
    assert _as_bool("true") is True
    assert _as_bool("不确定") is None


@pytest.mark.asyncio
async def test_dr08_redirect_to_private_address_is_blocked() -> None:
    from rsshub.errors import InvalidFeedUrlError
    from rsshub.infrastructure.fetcher.client import HttpClient

    client = HttpClient(timeout=1.0, user_agent="t", allow_private=False)
    with pytest.raises(InvalidFeedUrlError):
        await client.fetch("http://127.0.0.1:9/internal")
    await client.close()


@pytest.mark.asyncio
async def test_dr08_redirect_hop_is_checked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """302 指向内网时必须在跳转后重新拦截，而不是只查起始地址。"""
    from rsshub import errors
    from rsshub.infrastructure.fetcher import client as client_module

    async def _fake_guard(url: str, *, allow_private: bool) -> str:
        if "169.254" in url or "127.0.0.1" in url:
            raise errors.InvalidFeedUrlError(f"不允许的地址: {url}")
        return url

    class _StubResponse:
        status_code = 302
        content = b""
        headers: ClassVar[dict] = {"location": "http://169.254.169.254/latest/meta-data/"}
        url = "https://example.com/feed.xml"

    class _StubClient:
        async def get(self, url: str, **kwargs: object) -> _StubResponse:
            return _StubResponse()

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(client_module, "guard_fetch_target", _fake_guard)
    client = client_module.HttpClient(timeout=1.0, user_agent="t", allow_private=False)
    client._client = _StubClient()  # type: ignore[assignment]

    with pytest.raises(errors.InvalidFeedUrlError):
        await client.fetch("https://example.com/feed.xml")
    await client.close()


@pytest.mark.asyncio
async def test_dr04_fetch_uses_configured_timeout() -> None:
    from rsshub.infrastructure.fetcher.client import HttpClient

    captured: dict = {}

    class _StubResponse:
        status_code = 200
        content = b"ok"
        headers: ClassVar[dict] = {}
        url = "https://example.com/feed.xml"

    class _StubClient:
        async def get(self, url: str, **kwargs: object) -> _StubResponse:
            captured.update(kwargs)
            return _StubResponse()

    client = HttpClient(timeout=7.5, user_agent="t")
    client._client = _StubClient()  # type: ignore[assignment]

    await client.fetch("https://example.com/feed.xml")

    assert captured["timeout"] == 7.5, "未显式传 timeout 时必须用构造时的配置值，而不是 None（禁用超时）"


@pytest.mark.asyncio
async def test_dr19_scheduler_keeps_other_groups_alive(store: Store, tmp_path: object) -> None:
    from rsshub.application.services.scheduler import Scheduler

    ctx = StubCtx(tmp_path)

    class _Poller:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def poll(self, feed: Feed, subs: list[Subscription]) -> object:
            self.seen.append(feed.link)
            if feed.link == "https://example.com/bad.xml":
                raise RuntimeError("boom")
            return SimpleNamespace(delivered=1, filtered=0, error="", new_entries=1)

    class _Dispatcher:
        async def retry_failed(self, limit: int = 50) -> int:
            return 0

    poller = _Poller()
    scheduler = Scheduler(ctx, store, make_config(), poller, _Dispatcher())  # type: ignore[arg-type]
    store.repos.users.get_or_create("u1")
    good = store.repos.feeds.save(Feed(link="https://example.com/good.xml"))
    bad = store.repos.feeds.save(Feed(link="https://example.com/bad.xml"))
    store.repos.subs.save(Subscription(user_id="u1", feed_id=good.id, target_session="s1", next_check_at=None))
    store.repos.subs.save(Subscription(user_id="u1", feed_id=bad.id, target_session="s1", next_check_at=None))

    await scheduler.tick()

    assert set(poller.seen) == {"https://example.com/good.xml", "https://example.com/bad.xml"}


@pytest.mark.asyncio
async def test_dr22_handlers_tools_check_ownership(store: Store, tmp_path: object) -> None:
    from tests.test_tools import _harness

    harness = _harness(tmp_path)
    feed = store.repos.feeds.save(Feed(link="https://example.com/feed.xml"))
    other = store.repos.subs.save(Subscription(user_id="someone-else", feed_id=feed.id, target_session="s2"))

    denied = await harness.rss_get_handlers(subscription_id=other.id, user_id="u1", stream_id="s1")
    assert denied["success"] is False
    denied_write = await harness.rss_set_subscription_handlers(
        subscription_id=other.id, handlers_json="[]", user_id="u1", stream_id="s1"
    )
    assert denied_write["success"] is False


def test_dr28_purge_includes_boundary_day(store: Store) -> None:
    from datetime import UTC, datetime, timedelta

    store.repos.history.add(PushHistory(user_id="u1", content="x", target_session="s1", status="success"))
    record = store.repos.history.list_recent(1)[0]
    cutoff = datetime.now(UTC).replace(microsecond=0) - timedelta(days=30)
    just_before = (cutoff - timedelta(seconds=1)).isoformat(timespec="seconds")
    store.db.execute("UPDATE rsshub_push_history SET created_at=? WHERE id=?", (just_before, record.id))

    removed = store.repos.history.purge_older_than(30)

    assert removed == 1, "created_at 带 T/时区、SQLite now 带空格，边界那天前的一秒也必须被清理"


def test_dr32_duplicate_feed_link_reuses_existing_row(store: Store) -> None:
    first = store.repos.feeds.save(Feed(link="https://example.com/feed.xml", title="甲"))
    second = store.repos.feeds.save(Feed(link="https://example.com/feed.xml", title="乙"))

    assert second.id == first.id
    assert store.repos.feeds.get_by_link("https://example.com/feed.xml").title == "甲"


def test_dr23_single_entry_text_shape() -> None:
    entry = Entry(title="标题", link="https://example.com/1", summary="摘要")

    text = build_entry_text("示例源", entry, _settings())

    assert text.startswith("【示例源】更新")
    assert "标题" in text and "摘要" in text


@pytest.mark.asyncio
async def test_dr15_import_requires_session_and_counts_duplicates(store: Store, tmp_path: object) -> None:
    from tests.test_web_api import Harness as WebHarness

    harness = WebHarness(StubCtx(tmp_path), store, FakeClient(content=RSS))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await harness.webui_import_subscriptions(links="https://example.com/feed.xml", session="")

    first = await harness.webui_import_subscriptions(links="https://example.com/feed.xml", session="s1")
    second = await harness.webui_import_subscriptions(links="https://example.com/feed.xml", session="s1")

    assert first == {"imported": 1, "duplicated": 0, "failed": []}
    assert second["imported"] == 0 and second["duplicated"] == 1


@pytest.mark.asyncio
async def test_dr09_disabling_plugin_stops_scheduler(tmp_path: object) -> None:
    from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF

    from rsshub.application.services.scheduler import Scheduler
    from tests.support import load_plugin_module

    plugin = load_plugin_module().create_plugin()
    plugin._set_context(StubCtx(tmp_path))  # type: ignore[arg-type]
    plugin.set_plugin_config({"plugin": {"config_version": "1.3.0", "enabled": True}})
    plugin.store = Store.open(tmp_path / "rsshub.db")  # type: ignore[operator]
    scheduler = Scheduler(plugin.ctx, plugin.store, plugin.config, None, None)  # type: ignore[arg-type]
    plugin._scheduler = scheduler

    plugin.set_plugin_config({"plugin": {"config_version": "1.3.0", "enabled": False}})
    await plugin.on_config_update(CONFIG_RELOAD_SCOPE_SELF, {}, "1.3.0")

    assert scheduler._running is False
    assert plugin.store is None, "关闭插件后应完成 teardown，且不重建"
