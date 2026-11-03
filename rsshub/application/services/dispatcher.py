"""推送执行：persona / direct / silent 三种模式与失败重试。

条目送达后按条目写 push_history（每条记录存该条目自己的文本，重试时也只重发这一条）；
再次推送同一条目前先查该订阅的成功记录，已送达的跳过，避免重试与重抓把内容发两遍。
persona 模式复用宿主麦麦的人格与表达选择生成一段话再发出；生成失败按配置回退直推。
silent 模式把内容注入麦麦的聊天上下文（不直接发言），并固定请它主动思考一轮要不要分享。
注入的图片先压到上下文预算内，并挂一个稳定短消息 ID：麦麦据此把图引用进自己的回复，
发不发由它决定。

富媒体：正文图与封面图随消息发出，多图走合并转发，表格渲染成图片。
音视频在后台推送里附链接行：宿主只为模型工具调用签发文件凭证，调度器推送没有凭证，
文件发送由工具侧的 ``_attach_av_files`` 承担。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from ...domain.entry import Entry
from ...domain.settings import EffectiveSettings
from ...infrastructure.media.compressor import CONTEXT_IMAGE_BYTES
from ...infrastructure.media.downloader import MediaBundle, MediaDownloader, av_link_card_text
from ...infrastructure.persistence.models import Feed, PushHistory, Subscription
from ...infrastructure.persistence.store import Store
from ...infrastructure.rendering.table import TableRenderer
from .message_format import build_direct_text, build_entry_text, build_reference, segments_from_text

PUSH_MODES = ("persona", "direct", "silent")

# 注入消息的短 ID 长度：宿主对 ≤12 字符的 ID 原样展示，麦麦在回复里直接引用它取图。
_CONTEXT_MESSAGE_ID_DIGITS = 8
_CONTEXT_MESSAGE_ID_PREFIX = "rss"


@dataclass
class PushReport:
    """一次推送的结果：是否送达、带了几张图、图为什么没带成。"""

    delivered: bool = False
    images: int = 0
    skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)

    def describe_media(self) -> str:
        """一句话说明附图情况，供命令回复与注入提示引用。"""
        if self.images and not self.skipped:
            return f"已附带 {self.images} 张图"
        if self.images:
            return f"已附带 {self.images} 张图，另有 {_describe_skips(self.skipped, self.skip_reasons)}"
        if self.skipped:
            return f"附图没取到：{_describe_skips(self.skipped, self.skip_reasons)}"
        return ""


def _describe_skips(count: int, reasons: dict[str, int]) -> str:
    detail = "、".join(f"{reason} {number}" for reason, number in reasons.items())
    return f"{count} 张（{detail}）" if detail else f"{count} 张"


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
                max_bytes=int(media_cfg.image_max_mib) * 1024 * 1024,
                timeout_seconds=float(getattr(media_cfg, "image_timeout_seconds", 20) or 20),
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
        feed: Feed | None,
        entries: list[Entry],
        settings: EffectiveSettings,
        stream_id: str,
        record: bool = True,
        source_type: str = "feed",
        source_key: str | None = None,
        mode_override: str | None = None,
        with_media: bool = True,
        extra_links: list[str] | None = None,
    ) -> bool:
        report = await self.push_report(
            sub=sub,
            feed=feed,
            entries=entries,
            settings=settings,
            stream_id=stream_id,
            record=record,
            source_type=source_type,
            source_key=source_key,
            mode_override=mode_override,
            with_media=with_media,
            extra_links=extra_links,
        )
        return report.delivered

    async def push_report(
        self,
        *,
        sub: Subscription,
        feed: Feed | None,
        entries: list[Entry],
        settings: EffectiveSettings,
        stream_id: str,
        record: bool = True,
        source_type: str = "feed",
        source_key: str | None = None,
        mode_override: str | None = None,
        with_media: bool = True,
        extra_links: list[str] | None = None,
    ) -> PushReport:
        if not entries:
            return PushReport(delivered=True)
        entries = [entry for entry in entries if not self._already_delivered(sub, entry)]
        if not entries:
            self._ctx.logger.info("RSS 推送跳过：这批条目在该会话已有成功记录 sub=%s", sub.id)
            return PushReport(delivered=True)
        feed_title = (feed.title if feed else "") or sub.title
        mode = mode_override or (settings.push_mode if settings.push_mode in PUSH_MODES else "direct")
        direct_text = build_direct_text(feed_title, entries, settings)
        started = time.monotonic()
        if not with_media:
            bundle = MediaBundle()
        elif mode == "silent":
            # 注入的图会留在聊天历史里，按上下文预算压缩后再注入
            bundle = await self._collect_context_media(entries, settings)
        else:
            bundle = await self._collect_media(entries, settings)
        media_elapsed = time.monotonic() - started
        report = PushReport(
            images=len(bundle.images),
            skipped=bundle.skipped,
            skip_reasons=dict(bundle.skip_reasons),
        )
        links = list(bundle.audio_video_links) + list(extra_links or [])
        if links:
            direct_text = f"{direct_text}\n{av_link_card_text(links)}"
        message_id = _context_message_id(entries)
        if mode == "silent" and bundle.images:
            direct_text = f"{direct_text}\n{_context_share_hint(message_id, len(bundle.images))}"
        if mode == "silent" and bundle.skipped:
            # 注入内容会被麦麦直接复述给群友：把「图没取到」写清楚，它才不会猜成别的原因
            direct_text = f"{direct_text}\n{_context_missing_hint(report)}"

        send_started = time.monotonic()
        if mode == "silent":
            delivered = await self._send_silent(stream_id, direct_text, entries, bundle.images, message_id)
        elif mode == "persona":
            delivered = await self._send_persona(stream_id, feed_title, entries, direct_text, bundle.images)
        else:
            delivered = await self._send_rich(stream_id, direct_text, bundle.images)
        send_elapsed = time.monotonic() - send_started
        report.delivered = delivered

        # 媒体与发送分两段计时：慢图与超时是这条链上最难从外部看到的耗时来源。
        self._ctx.logger.info(
            "RSS 推送 mode=%s entries=%d images=%d skipped=%d media=%.1fs send=%.1fs ok=%s",
            mode,
            len(entries),
            len(bundle.images),
            bundle.skipped,
            media_elapsed,
            send_elapsed,
            delivered,
        )
        if bundle.skipped:
            self._ctx.logger.info("RSS 附图跳过 %s", _describe_skips(bundle.skipped, bundle.skip_reasons))

        if record:
            for entry in entries:
                text = build_entry_text(feed_title, entry, settings)
                self._record(
                    sub,
                    feed,
                    entry,
                    text,
                    stream_id,
                    delivered,
                    source_type=source_type,
                    source_key=source_key or self._default_source_key(feed, sub),
                )
        return report

    def _already_delivered(self, sub: Subscription, entry: Entry) -> bool:
        if sub.id is None or not sub.target_session:
            return False
        return self._store.repos.history.count_for_entry(entry.dedup_key(), sub.target_session) > 0

    async def retry_failed(self, limit: int = 50) -> int:
        """重发失败记录：每条记录存的是单条目文本，这里也只重发这一条。"""
        records = self._store.repos.history.list_retryable(limit)
        recovered = 0
        for record in records:
            if not record.target_session:
                self._store.repos.history.update(record.id, status="stopped", fail_reason="缺少目标聊天流")
                continue
            if record.sub_id is not None and self._store.repos.subs.get(record.sub_id) is None:
                # 订阅已被删除，不再往旧会话重发
                self._store.repos.history.update(record.id, status="stopped", fail_reason="订阅已删除")
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
        # display_media: 2 表示不带媒体，0/1 按上限带图。
        if settings.display_media == 2 or settings.display_media < 0 or self._downloader is None:
            return MediaBundle()
        bundle = await self._downloader.collect(entries)
        media_cfg = getattr(self._config, "media", None)
        limit = int(getattr(media_cfg, "max_images", 0) or 0)
        if limit == 0:
            # 张数上限为 0 即不带任何图片，表格也不再渲染成图
            return bundle
        for entry in entries:
            if len(bundle.images) >= limit:
                break
            rendered = await self._tables.render(entry.content_html)
            if rendered:
                bundle.images.insert(0, rendered)
        return bundle

    async def _collect_context_media(self, entries: list[Entry], settings: EffectiveSettings) -> MediaBundle:
        """注入模式只取正文图与封面图：表格渲染与音视频链接不随注入进上下文。"""
        if settings.display_media == 2 or settings.display_media < 0 or self._downloader is None:
            return MediaBundle()
        return await self._downloader.collect(entries, image_budget_bytes=CONTEXT_IMAGE_BYTES)

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
        self, stream_id: str, feed_title: str, entries: list[Entry], direct_text: str, images: list[str]
    ) -> bool:
        push_cfg = self._config.push
        reference = build_reference(feed_title, entries, int(push_cfg.max_reference_chars))
        try:
            result = await self._ctx.maisaka.reply.generate(
                stream_id,
                reference=reference,
                format_instruction=str(self._config.persona.format_instruction or ""),
                timeout_ms=int(push_cfg.persona_timeout_seconds) * 1000,
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

    async def _send_silent(
        self,
        stream_id: str,
        text: str,
        entries: list[Entry],
        images: list[str],
        message_id: str,
    ) -> bool:
        segments = segments_from_text(text)
        segments.extend(_context_image_segments(images, entries))
        try:
            await self._ctx.maisaka.context.append(
                stream_id,
                segments,
                visible_text=entries[0].title if entries else "",
                source_kind="rsshub",
                message_id=message_id,
                replyable=False,
            )
        except Exception as exc:
            self._ctx.logger.warning("RSS 注入失败: %s", exc)
            return False

        # 注入模式固定请麦麦想一轮：说不说由它自己决定，这里只负责把话头递过去。
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
        feed: Feed | None,
        entry: Entry,
        content: str,
        stream_id: str,
        delivered: bool,
        *,
        source_type: str = "feed",
        source_key: str = "",
    ) -> None:
        record = PushHistory(
            user_id=sub.user_id,
            sub_id=sub.id,
            feed_id=feed.id if feed else None,
            source_type=source_type,
            source_key=source_key,
            content=content,
            raw_xml=entry.raw_xml,
            entry_title=entry.title,
            entry_link=entry.link,
            entry_guid=entry.dedup_key(),
            feed_title=(feed.title if feed else "") or sub.title,
            feed_link=feed.link if feed else "",
            platform_name=sub.platform_name,
            target_session=stream_id,
            status="success" if delivered else "failed",
            fail_reason=None if delivered else "发送失败",
            max_retries=3,
        )
        self._store.repos.history.add(record)

    @staticmethod
    def _default_source_key(feed: Feed | None, sub: Subscription) -> str:
        if feed is None:
            return f"sub:{sub.id}"
        return f"feed:{feed.id}:sub:{sub.id}"


def _context_share_hint(message_id: str, image_count: int) -> str:
    """告诉麦麦这张图挂在哪条消息上：宿主的 reply 工具按 msg_id + index 取图。"""
    indices = "index=0" if image_count == 1 else f"index=0..{image_count - 1}"
    return f'需要分享时可以把图一起带上：msg_id="{message_id}"，{indices}。'


def _context_missing_hint(report: PushReport) -> str:
    """图没取到时同样给出结论：麦麦照这句回答，不会再说成「没挂抓图功能」。"""
    return f"（本轮{report.describe_media()}）"


def _context_image_segments(images: list[str], entries: list[Entry]) -> list[dict[str, Any]]:
    """注入用的图片段：宿主按 ``binary_data_base64`` 还原图片，``description`` 进图的说明。"""
    if not images:
        return []
    description = entries[0].title if entries else ""
    return [{"type": "image", "binary_data_base64": image, "description": description} for image in images]


def _context_message_id(entries: list[Entry]) -> str:
    """注入消息的稳定短 ID：麦麦在回复里引用它取图，长度控制在宿主原样展示的范围内。"""
    seed = entries[0].dedup_key() if entries else ""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_CONTEXT_MESSAGE_ID_DIGITS]
    return f"{_CONTEXT_MESSAGE_ID_PREFIX}{digest}"


def _now() -> str:
    from ...infrastructure.persistence.database import utcnow

    return utcnow()
