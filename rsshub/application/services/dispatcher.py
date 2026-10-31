"""推送执行：persona / direct / silent 三种模式与失败重试。

推送语义（与源插件的差异见 README）：条目送达即写 push_history。
persona 模式复用宿主麦麦的人格与表达选择生成一段话再发出；生成失败按配置回退直推。
silent 模式不主动说话，把内容注入麦麦的聊天上下文，可选再请求一轮主动思考。
"""

from __future__ import annotations

from typing import Any

from ...domain.entry import Entry
from ...domain.settings import EffectiveSettings
from ...infrastructure.persistence.models import Feed, PushHistory, Subscription
from ...infrastructure.persistence.store import Store
from .message_format import build_direct_text, build_reference, segments_from_text

PUSH_MODES = ("persona", "direct", "silent")


class Dispatcher:
    """把一批新条目送达目标聊天流，并记录推送历史。"""

    def __init__(self, ctx: Any, store: Store, config: Any) -> None:
        self._ctx = ctx
        self._store = store
        self._config = config

    # ── 对外入口 ────────────────────────────────────────────────

    async def push(
        self,
        *,
        sub: Subscription,
        feed: Feed,
        entries: list[Entry],
        settings: EffectiveSettings,
        stream_id: str,
    ) -> bool:
        if not entries:
            return True
        mode = settings.push_mode if settings.push_mode in PUSH_MODES else "direct"
        direct_text = build_direct_text(feed.title or sub.title, entries, settings)

        if mode == "silent":
            delivered = await self._send_silent(stream_id, direct_text, entries)
        elif mode == "persona":
            delivered = await self._send_persona(stream_id, feed, entries, direct_text)
        else:
            delivered = await self._send_text(stream_id, direct_text)

        for entry in entries:
            self._record(sub, feed, entry, direct_text, stream_id, delivered)
        return delivered

    async def retry_failed(self, limit: int = 50) -> int:
        """重发失败记录（直推内容原文重发）。"""
        records = self._store.repos.history.list_retryable(limit)
        recovered = 0
        for record in records:
            if not record.target_session:
                self._store.repos.history.update(record.id, status="stopped", fail_reason="缺少目标聊天流")
                continue
            if await self._send_text(record.target_session, record.content):
                self._store.repos.history.update(record.id, status="success", fail_reason=None, completed_at=_now())
                recovered += 1
            else:
                retries = record.retry_count + 1
                status = "failed" if retries < record.max_retries else "stopped"
                self._store.repos.history.update(
                    record.id,
                    status=status,
                    retry_count=retries,
                    fail_reason="重发失败",
                )
        return recovered

    # ── 三种发送形态 ────────────────────────────────────────────

    async def _send_text(self, stream_id: str, text: str) -> bool:
        try:
            result = await self._ctx.send.text(text, stream_id)
            return bool(result.get("sent")) if isinstance(result, dict) else bool(result)
        except Exception as exc:
            self._ctx.logger.warning("RSS 推送发送失败: %s", exc)
            return False

    async def _send_persona(self, stream_id: str, feed: Feed, entries: list[Entry], direct_text: str) -> bool:
        push_cfg = self._config.push
        reference = build_reference(feed.title, entries, int(push_cfg.max_reference_chars))
        try:
            result = await self._ctx.maisaka.reply.generate(
                stream_id,
                reference=reference,
                format_instruction=str(self._config.persona.format_instruction or ""),
                timeout_ms=int(push_cfg.persona_timeout_ms),
            )
        except Exception as exc:
            self._ctx.logger.warning("RSS 人格化生成失败: %s", exc)
            result = None

        text = ""
        if isinstance(result, dict) and result.get("success") is not False:
            text = str(result.get("text") or "").strip()
        if text:
            if await self._send_text(stream_id, text):
                return True
        if push_cfg.persona_fallback_to_direct:
            self._ctx.logger.info("RSS 人格化未产出可用文本，回退直推")
            return await self._send_text(stream_id, direct_text)
        return False

    async def _send_silent(self, stream_id: str, text: str, entries: list[Entry]) -> bool:
        try:
            await self._ctx.maisaka.context.append(
                stream_id,
                segments_from_text(text),
                visible_text=entries[0].title if entries else "",
                source_kind="rsshub",
                replyable=False,
            )
        except Exception as exc:
            self._ctx.logger.warning("RSS 静默注入失败: %s", exc)
            return False

        if self._config.silent.proactive_trigger:
            try:
                await self._ctx.maisaka.proactive.trigger(
                    stream_id,
                    intent="订阅源有新内容，考虑是否主动分享给群友",
                    reason="rsshub:silent_push",
                    metadata={"entry_count": len(entries)},
                )
            except Exception as exc:
                self._ctx.logger.warning("RSS 主动思考触发失败: %s", exc)
        return True

    # ── 历史记录 ────────────────────────────────────────────────

    def _record(
        self,
        sub: Subscription,
        feed: Feed,
        entry: Entry,
        content: str,
        stream_id: str,
        delivered: bool,
    ) -> None:
        record = PushHistory(
            user_id=sub.user_id,
            sub_id=sub.id,
            feed_id=feed.id,
            source_type="feed",
            source_key=f"feed:{feed.id}:sub:{sub.id}",
            content=content,
            raw_xml=entry.raw_xml,
            entry_title=entry.title,
            entry_link=entry.link,
            entry_guid=entry.dedup_key(),
            feed_title=feed.title or sub.title,
            feed_link=feed.link,
            platform_name=sub.platform_name,
            target_session=stream_id,
            status="success" if delivered else "failed",
            fail_reason=None if delivered else "发送失败",
            max_retries=3,
        )
        self._store.repos.history.add(record)


def _now() -> str:
    from ...infrastructure.persistence.database import utcnow

    return utcnow()
