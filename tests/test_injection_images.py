"""注入模式带图：先压到上下文预算，再挂可被麦麦引用的消息 ID 与分享提示。"""

from __future__ import annotations

import base64
import io
import random
from types import SimpleNamespace

import pytest
from PIL import Image

from rsshub.application.services.dispatcher import Dispatcher
from rsshub.domain.entry import Enclosure, Entry
from rsshub.domain.settings import EffectiveSettings
from rsshub.infrastructure.media.compressor import CONTEXT_IMAGE_BYTES, compress_image
from rsshub.infrastructure.media.downloader import MediaDownloader
from rsshub.infrastructure.persistence.models import Feed, Subscription
from rsshub.infrastructure.persistence.store import Store
from tests.support import FakeClient, StubCtx, make_config

IMAGE_URL = "https://example.com/pixiv/109808763.jpg"


@pytest.fixture
def store(tmp_path: object) -> Store:
    return Store.open(tmp_path / "rsshub.db")  # type: ignore[operator]


def _noisy_jpeg(side: int = 1200) -> bytes:
    """接近照片的随机噪点图：JPEG 压不小，用来触发预算压缩。"""
    rng = random.Random(7)
    pixels = bytes(rng.getrandbits(8) for _ in range(side * side * 3))
    buffer = io.BytesIO()
    Image.frombytes("RGB", (side, side), pixels).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _tiny_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _settings(mode: str = "silent", display_media: int = 0) -> EffectiveSettings:
    return EffectiveSettings(
        interval_seconds=600,
        notify=True,
        push_mode=mode,
        length_limit=0,
        display_author=0,
        display_via=0,
        display_title=0,
        display_entry_tags=0,
        style=0,
        display_media=display_media,
    )


def _entry_with_image() -> Entry:
    return Entry(
        title="示例图",
        link="https://example.com/post/7",
        enclosures=[Enclosure(url=IMAGE_URL, type="image/jpeg")],
    )


def test_compress_image_fits_context_budget() -> None:
    payload = _noisy_jpeg()
    assert len(payload) > CONTEXT_IMAGE_BYTES, "夹具本身要超过预算，否则测不到压缩"

    compressed = compress_image(payload)

    assert compressed is not None
    assert len(compressed) <= CONTEXT_IMAGE_BYTES
    assert len(compressed) < len(payload)
    with Image.open(io.BytesIO(compressed)) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= 1600


def test_compress_image_keeps_payload_already_within_budget() -> None:
    payload = _tiny_png()

    assert compress_image(payload) == payload


def test_compress_image_returns_none_when_budget_unreachable() -> None:
    assert compress_image(_noisy_jpeg(), max_bytes=512) is None


@pytest.mark.asyncio
async def test_downloader_compresses_when_budget_is_given() -> None:
    client = FakeClient(headers={"content-type": "image/jpeg"}, payloads={IMAGE_URL: _noisy_jpeg()})
    downloader = MediaDownloader(client, max_images=1, max_bytes=8 * 1024 * 1024)  # type: ignore[arg-type]

    bundle = await downloader.collect([_entry_with_image()], image_budget_bytes=CONTEXT_IMAGE_BYTES)

    assert len(bundle.images) == 1
    decoded = base64.b64decode(bundle.images[0])
    assert len(decoded) <= CONTEXT_IMAGE_BYTES
    assert decoded.startswith(b"\xff\xd8\xff"), "压缩后统一输出 JPEG"


@pytest.mark.asyncio
async def test_silent_push_injects_image_with_referenceable_message_id(store: Store, tmp_path: object) -> None:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    store.repos.users.get_or_create("u1")
    feed = store.repos.feeds.save(Feed(link="https://example.com/feed.xml", title="示例源"))
    sub = store.repos.subs.save(Subscription(user_id="u1", feed_id=feed.id, target_session="s1"))
    media = SimpleNamespace(
        max_images=1,
        image_max_mib=8,
        table_render_enabled=False,
        multi_image_forward=True,
        attach_files=True,
        max_files=2,
        file_max_mib=20,
    )
    config = make_config(media=media)
    dispatcher = Dispatcher(
        ctx, store, config, FakeClient(headers={"content-type": "image/jpeg"}, payloads={IMAGE_URL: _noisy_jpeg()})
    )

    delivered = await dispatcher.push(
        sub=sub, feed=feed, entries=[_entry_with_image()], settings=_settings(), stream_id="s1"
    )

    assert delivered is True
    call = ctx.mai_append_calls[0]
    assert call["replyable"] is False
    assert ctx.send.sent == [], "注入模式不直接往会话发图"
    assert call["message_id"].startswith("rss")
    assert len(call["message_id"]) <= 12, "宿主对 ≤12 字符的消息 ID 原样展示，麦麦才能直接引用"
    image_segments = [segment for segment in call["segments"] if segment["type"] == "image"]
    assert len(image_segments) == 1
    assert len(base64.b64decode(image_segments[0]["binary_data_base64"])) <= CONTEXT_IMAGE_BYTES
    assert "需要分享" in call["segments"][0]["content"], "注入文本要提示麦麦可以把图一起发出来"
    assert len(ctx.mai_proactive_calls) == 1


@pytest.mark.asyncio
async def test_silent_push_without_images_has_no_hint(store: Store, tmp_path: object) -> None:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    store.repos.users.get_or_create("u1")
    feed = store.repos.feeds.save(Feed(link="https://example.com/feed.xml", title="示例源"))
    sub = store.repos.subs.save(Subscription(user_id="u1", feed_id=feed.id, target_session="s1"))
    dispatcher = Dispatcher(ctx, store, make_config(), FakeClient())

    await dispatcher.push(
        sub=sub,
        feed=feed,
        entries=[Entry(title="纯文字", link="https://example.com/1")],
        settings=_settings(),
        stream_id="s1",
    )

    call = ctx.mai_append_calls[0]
    assert [segment["type"] for segment in call["segments"]] == ["text"]
    assert "需要分享" not in call["segments"][0]["content"]
