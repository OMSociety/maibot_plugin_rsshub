"""推送执行：persona / direct / silent 三种模式与失败重试。

推送语义（与源插件的差异见 README）：条目送达即写 push_history。
persona 模式复用宿主麦麦的人格与表达选择生成一段话再发出；生成失败按配置回退直推。
silent 模式不主动说话，把内容注入麦麦的聊天上下文，可选再请求一轮主动思考。

富媒体（二期）：正文图与封面图随消息发出，多图走合并转发，表格渲染成图片。
音视频在后台推送里附链接行：宿主只为模型工具调用签发文件凭证，调度器推送没有凭证，
文件发送由工具侧的 ``_attach_av_files`` 承担。
"""

from __future__ import annotations

from typing import Any

from ...domain.entry import Entry
from ...domain.settings import EffectiveSettings
from ...infrastructure.media.downloader import MediaBundle, MediaDownloader, av_link_card_text
from ...infrastructure.persistence.models import Feed, PushHistory, Subscription
from ...infrastructure.persistence.store import Store
from ...infrastructure.rendering.table import TableRenderer
from .message_format import build_direct_text, build_reference, segments_from_text

PUSH_MODES = ("persona", "direct", "silent")


class Dispatcher:
    """把一批新条目送达目标聊天流，并记录推送历史。"""

    def __init__(self, ctx: Any, store: Store, config: Any, client: Any = None) -> None:
        self._ctx = ctx
        self._store = store
        self._config = config
        media_cfg = getattr(config, "media", None)
        self._downloader = (
            MediaDownloader(
                client,
                max_images=int(media_cfg.max_images),
                max_bytes=int(media_cfg.image_max_bytes),
            )
            if client is not None and media_cfg is not None
            else None
        )
        self._tables = TableRenderer(
            ctx,
            enabled=bool(getattr(media_cfg, "table_render_enabled", False)),
        )

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
        bundle = MediaBundle() if mode == "silent" else await self._collect_media(entries, settings)
        if bundle.audio_video_links:
            direct_text = f"{direct_text}\n{av_link_card_text(bundle.audio_video_links)}"

        if mode == "silent":
            delivered = await self._send_silent(stream_id, direct_text, entries)
        elif mode == "persona":
            delivered = await self._send_persona(stream_id, feed, entries, direct_text, bundle.images)
        else:
            delivered = await self._send_rich(stream_id, direct_text, bundle.images)

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

    # ── 媒体 ────────────────────────────────────────────────────

    async def _collect_media(self, entries: list[Entry], settings: EffectiveSettings) -> MediaBundle:
        if settings.display_media < 0 or self._downloader is None:
            return MediaBundle()
        bundle = await self._downloader.collect(entries)
        media_cfg = getattr(self._config, "media", None)
        limit = int(getattr(media_cfg, "max_images", 0) or 0)
        for entry in entries:
            if limit and len(bundle.images) >= limit:
                break
            rendered = await self._tables.render(entry.content_html)
            if rendered:
                bundle.images.insert(0, rendered)
        return bundle

    # ── 三种发送形态 ────────────────────────────────────────────

    async def _send_text(self, stream_id: str, text: str) -> bool:
        try:
            result = await self._ctx.send.text(text, stream_id)
            return bool(result.get("sent")) if isinstance(result, dict) else bool(result)
        except Exception as exc:
            self._ctx.logger.warning("RSS 推送发送失败: %s", exc)
            return False

    async def _send_rich(self, stream_id: str, text: str, images: list[str]) -> bool:
        if not images:
            return await self._send_text(stream_id, text)
        media_cfg = getattr(self._config, "media", None)
        if len(images) > 1 and bool(getattr(media_cfg, "multi_image_forward", True)):
            return await self._send_forward(stream_id, text, images)
        return await self._send_hybrid(stream_id, text, images)

    async def _send_hybrid(self, stream_id: str, text: str, images: list[str]) -> bool:
        segments: list[dict[str, Any]] = [{"type": "text", "content": text}]
        segments.extend({"type": "image", "content": image} for image in images)
        try:
            result = await self._ctx.send.hybrid(segments, stream_id)
            return bool(result.get("sent")) if isinstance(result, dict) else bool(result)
        except Exception as exc:
            self._ctx.logger.warning("RSS 图文混排发送失败，回退纯文本: %s", exc)
            return await self._send_text(stream_id, text)

    async def _send_forward(self, stream_id: str, text: str, images: list[str]) -> bool:
        segments: list[dict[str, Any]] = [{"type": "text", "content": text}]
        segments.extend({"type": "image", "content": image} for image in images)
        messages = [{"nickname": "RSS 订阅", "content": segments}]
        try:
            result = await self._ctx.send.forward(messages, stream_id)
            return bool(result.get("sent")) if isinstance(result, dict) else bool(result)
        except Exception as exc:
            self._ctx.logger.warning("RSS 合并转发失败，回退图文混排: %s", exc)
            return await self._send_hybrid(stream_id, text, images)

    async def _send_persona(
        self, stream_id: str, feed: Feed, entries: list[Entry], direct_text: str, images: list[str]
    ) -> bool:
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
            if await self._send_rich(stream_id, text, images):
                return True
        if push_cfg.persona_fallback_to_direct:
            self._ctx.logger.info("RSS 人格化未产出可用文本，回退直推")
            return await self._send_rich(stream_id, direct_text, images)
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
