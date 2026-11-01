"""单次轮询：抓取 → 解析 → 去重 → 内容处理 → 推送 → 落库。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ...domain.entry import Entry
from ...domain.settings import GlobalDefaults, resolve_settings
from ...errors import FeedFetchError
from ...infrastructure.fetcher.client import HttpClient
from ...infrastructure.fetcher.dedup import entry_hash_set, is_known_entry, merge_hashes
from ...infrastructure.fetcher.parser import parse_feed
from ...infrastructure.persistence.models import Feed, Subscription
from ...infrastructure.persistence.store import Store
from .content_handler import ContentHandlerChain, HandlerContext
from .dispatcher import Dispatcher


@dataclass
class PollOutcome:
    feed_id: int
    not_modified: bool = False
    new_entries: int = 0
    delivered: int = 0
    filtered: int = 0
    error: str = ""


def build_defaults(config: Any) -> GlobalDefaults:
    return GlobalDefaults(
        interval_seconds=int(config.polling.interval_minutes) * 60,
        default_push_mode=str(config.push.default_mode),
    )


def _now() -> datetime:
    return datetime.now(UTC)


class FeedPoller:
    """按 feed 抓取一次并推送给该 feed 下所有订阅。"""

    def __init__(
        self,
        ctx: Any,
        store: Store,
        config: Any,
        client: HttpClient,
        handlers: ContentHandlerChain,
        dispatcher: Dispatcher,
    ) -> None:
        self._ctx = ctx
        self._store = store
        self._config = config
        self._client = client
        self._handlers = handlers
        self._dispatcher = dispatcher
        self._defaults = build_defaults(config)

    async def poll(self, feed: Feed, subs: list[Subscription]) -> PollOutcome:
        outcome = PollOutcome(feed_id=feed.id or 0)
        try:
            result = await self._client.fetch(feed.link, etag=feed.etag, last_modified=feed.last_modified)
        except FeedFetchError as exc:
            outcome.error = str(exc)
            self._ctx.logger.warning("RSS 抓取失败 %s: %s", feed.link, exc)
            self._advance_schedule(subs)
            return outcome

        if result.not_modified:
            outcome.not_modified = True
            self._advance_schedule(subs)
            return outcome

        try:
            document = parse_feed(result.content, result.url)
        except FeedFetchError as exc:
            outcome.error = str(exc)
            self._ctx.logger.warning("RSS 解析失败 %s: %s", feed.link, exc)
            self._advance_schedule(subs)
            return outcome

        known = set(feed.entry_hashes)
        bootstrap = not feed.entry_hashes
        new_entries = [entry for entry in document.entries if not is_known_entry(entry, known)]
        outcome.new_entries = len(new_entries)

        if bootstrap:
            # 首次抓取只建基线，不把历史条目全推一遍
            self._ctx.logger.info("RSS 首次抓取 %s，记录 %d 条基线", feed.link, len(new_entries))
            self._persist_feed(feed, document, result, new_entries, push_ok=True)
            self._advance_schedule(subs)
            return outcome

        limit = int(self._config.polling.max_entries_per_poll)
        new_entries.sort(key=lambda item: item.published or datetime.min.replace(tzinfo=UTC), reverse=True)
        selected = list(reversed(new_entries[:limit]))
        all_ok = True
        for sub in subs:
            if sub.target_session is None:
                continue
            user = self._store.repos.users.get(sub.user_id)
            settings = resolve_settings(sub, user, self._defaults)
            if not settings.notify:
                continue
            delivered, filtered = await self._push_subscription(sub, document.title, selected, settings)
            outcome.delivered += delivered
            outcome.filtered += filtered
            all_ok = all_ok and delivered + filtered == len(selected)

        # 只有全部订阅都完成投递才推进 etag/哈希；否则下轮重抓，靠 push_history 幂等兜底
        self._persist_feed(feed, document, result, new_entries, push_ok=all_ok)
        self._advance_schedule(subs)
        return outcome

    # ── 内部 ────────────────────────────────────────────────────

    async def _push_subscription(
        self,
        sub: Subscription,
        feed_title: str,
        entries: list[Entry],
        settings: Any,
    ) -> tuple[int, int]:
        stream_id = sub.target_session or ""
        if not stream_id:
            return 0, 0

        feed = self._store.repos.feeds.get(sub.feed_id)
        if feed is None:
            return 0, 0

        accepted: list[Entry] = []
        delivered = 0
        filtered = 0
        for entry in entries:
            context = HandlerContext.from_entry(entry, feed_title=feed_title, feed_link=feed.link)
            outcome = await self._handlers.run(settings.handlers, context, session_id=stream_id)
            if not outcome.allow:
                filtered += 1
                self._record_skipped(sub, entry, outcome.trace)
                continue
            edited = Entry(
                title=outcome.context.title,
                link=entry.link,
                author=entry.author,
                content=outcome.context.content,
                summary=outcome.context.summary,
                guid=entry.guid,
                entry_id=entry.entry_id,
                tags=entry.tags,
                enclosures=entry.enclosures,
                published=entry.published,
                updated=entry.updated,
                raw_xml=outcome.context.raw_xml,
            )
            accepted.append(edited)

        if accepted:
            ok = await self._dispatcher.push(
                sub=sub, feed=feed, entries=accepted, settings=settings, stream_id=stream_id
            )
            delivered = len(accepted) if ok else 0
        return delivered, filtered

    def _record_skipped(self, sub: Subscription, entry: Entry, trace: list[dict[str, Any]]) -> None:
        from ...infrastructure.persistence.models import PushHistory

        feed = self._store.repos.feeds.get(sub.feed_id)
        record = PushHistory(
            user_id=sub.user_id,
            sub_id=sub.id,
            feed_id=sub.feed_id,
            content="",
            raw_xml=entry.raw_xml,
            handler_trace=trace,
            entry_title=entry.title,
            entry_link=entry.link,
            entry_guid=entry.dedup_key(),
            feed_title=feed.title if feed else "",
            feed_link=feed.link if feed else "",
            platform_name=sub.platform_name,
            target_session=sub.target_session,
            status="skipped",
            max_retries=0,
            fail_reason="内容处理链拦截",
        )
        self._store.repos.history.add(record)

    def _persist_feed(self, feed: Feed, document: Any, result: Any, entries: list[Entry], *, push_ok: bool) -> None:
        hashes: list[str] = []
        for entry in entries:
            hashes.extend(entry_hash_set(entry))
        feed.entry_hashes = merge_hashes(feed.entry_hashes, hashes)
        feed.title = feed.title or document.title
        if push_ok:
            feed.etag = result.headers.get("ETag") or feed.etag
            feed.last_modified = result.headers.get("Last-Modified") or feed.last_modified
        self._store.repos.feeds.save(feed)

    def _advance_schedule(self, subs: list[Subscription]) -> None:
        base_defaults = self._defaults
        for sub in subs:
            user = self._store.repos.users.get(sub.user_id)
            settings = resolve_settings(sub, user, base_defaults)
            next_at = (_now() + timedelta(seconds=settings.interval_seconds)).isoformat(timespec="seconds")
            self._store.repos.subs.update(sub.id, next_check_at=next_at)
