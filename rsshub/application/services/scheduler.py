"""轮询调度：常驻 asyncio 任务，按订阅到期时间抓取。

宿主没有 cron 能力，调度器由插件在 on_load 自起；每 30 秒扫一次到期订阅，
同一 feed 下的多个订阅合并成一次抓取。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from ...infrastructure.persistence.models import Feed, Subscription
from ...infrastructure.persistence.store import Store
from .dispatcher import Dispatcher
from .polling import FeedPoller


class Scheduler:
    """自旋循环调度器。"""

    TICK_SECONDS = 30
    RETRY_BATCH = 50
    PURGE_DAYS = 30

    def __init__(
        self,
        ctx: Any,
        store: Store,
        config: Any,
        poller: FeedPoller,
        dispatcher: Dispatcher,
    ) -> None:
        self._ctx = ctx
        self._store = store
        self._config = config
        self._poller = poller
        self._dispatcher = dispatcher
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._last_purge_day: int | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="rsshub-scheduler")
        self._ctx.logger.info("RSS 调度器已启动，tick=%ss", self.TICK_SECONDS)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._ctx.logger.info("RSS 调度器已停止")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._ctx.logger.exception("RSS 调度轮次异常，本轮跳过")
            await asyncio.sleep(self.TICK_SECONDS)

    async def tick(self) -> None:
        await self._dispatcher.retry_failed(self.RETRY_BATCH)
        self._maybe_purge()

        groups = self._due_groups()
        if not groups:
            return

        semaphore = asyncio.Semaphore(max(1, int(self._config.polling.concurrency)))

        async def run_group(feed: Feed, subs: list[Subscription]) -> None:
            async with semaphore:
                outcome = await self._poller.poll(feed, subs)
                if outcome.delivered or outcome.error:
                    self._ctx.logger.info(
                        "RSS 轮询 feed=%s new=%s delivered=%s filtered=%s err=%s",
                        feed.link,
                        outcome.new_entries,
                        outcome.delivered,
                        outcome.filtered,
                        outcome.error or "-",
                    )

        # 一组抛出的非 FeedFetchError 异常不应该吃掉同轮其它组的投递结果；
        # 但 gather 会把它们收进返回值，所以这里逐条记日志，否则组内异常彻底无声。
        results = await asyncio.gather(*(run_group(feed, subs) for feed, subs in groups), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                self._ctx.logger.opt(exception=result).error("RSS 轮询组异常退出: %r", result)

    # ── 内部 ────────────────────────────────────────────────────

    def _due_groups(self) -> list[tuple[Feed, list[Subscription]]]:
        now = datetime.now(UTC)
        grouped: dict[int, list[Subscription]] = defaultdict(list)
        for sub in self._store.repos.subs.list_active():
            if not self._is_due(sub, now):
                continue
            grouped[sub.feed_id].append(sub)

        result: list[tuple[Feed, list[Subscription]]] = []
        for feed_id, subs in grouped.items():
            feed = self._store.repos.feeds.get(feed_id)
            if feed is None:
                continue
            result.append((feed, subs))
        return result

    @staticmethod
    def _is_due(sub: Subscription, now: datetime) -> bool:
        if sub.state != 1 or not sub.target_session:
            return False
        if not sub.next_check_at:
            return True
        try:
            return datetime.fromisoformat(sub.next_check_at) <= now
        except ValueError:
            return True

    def _maybe_purge(self) -> None:
        today = datetime.now(UTC).toordinal()
        if self._last_purge_day == today:
            return
        self._last_purge_day = today
        removed = self._store.repos.history.purge_older_than(self.PURGE_DAYS)
        orphan_feeds = self._store.repos.history.purge_orphan_feeds(self.PURGE_DAYS)
        if removed or orphan_feeds:
            self._ctx.logger.info(
                "RSS 清理 %s 天前推送历史 %d 条、无订阅订阅源 %d 个", self.PURGE_DAYS, removed, orphan_feeds
            )
