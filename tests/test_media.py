"""富媒体：图片候选、下载限额、图文混排与合并转发、表格渲染、音视频链接卡。"""

from __future__ import annotations

import base64
import time

import pytest

from rsshub.application.services.dispatcher import Dispatcher
from rsshub.domain.entry import Entry
from rsshub.domain.settings import EffectiveSettings
from rsshub.infrastructure.media.downloader import (
    SKIP_TIMEOUT,
    SKIP_TOO_LARGE,
    SKIP_UNDECODABLE,
    FileAttachments,
    MediaDownloader,
    av_link_card_text,
    collect_media_candidates,
)
from rsshub.infrastructure.persistence.models import Feed, Subscription
from rsshub.infrastructure.persistence.store import Store
from tests.support import FakeClient, StubCtx, make_config

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
BIG = b"\x89PNG\r\n\x1a\n" + b"0" * 4096


def _settings(mode: str = "direct", display_media: int = 0) -> EffectiveSettings:
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


def _entry(**kwargs: object) -> Entry:
    base = {"title": "标题", "link": "https://example.com/post/1"}
    base.update(kwargs)
    return Entry(**base)


def test_collect_media_candidates_from_html_and_enclosures() -> None:
    entry = _entry(
        content_html='<p>正文</p><img src="/img/a.png"><img src="https://cdn.example.com/b.jpg">',
    )
    images, av = collect_media_candidates(entry)

    assert images == ["https://example.com/img/a.png", "https://cdn.example.com/b.jpg"]
    assert av == []


def test_av_links_become_link_card_text() -> None:
    entry = _entry(content_html='<video src="/v/demo.mp4"></video>')
    images, av = collect_media_candidates(entry)

    assert images == []
    assert av == ["https://example.com/v/demo.mp4"]
    text = av_link_card_text(av)
    assert "音视频" in text and "demo.mp4" in text


@pytest.mark.asyncio
async def test_downloader_respects_limits_and_skips_oversized() -> None:
    client = FakeClient(payloads={"https://example.com/a.png": PNG, "https://example.com/b.png": BIG})
    downloader = MediaDownloader(client, max_images=2, max_bytes=1024)

    bundle = await downloader.collect(
        [_entry(content_html='<img src="https://example.com/a.png"><img src="https://example.com/b.png">')]
    )

    assert len(bundle.images) == 1
    assert base64.b64decode(bundle.images[0]) == PNG
    assert bundle.skipped == 1
    assert bundle.skip_reasons == {SKIP_TOO_LARGE: 1}


@pytest.mark.asyncio
async def test_slow_image_is_skipped_within_budget() -> None:
    """慢图不能把整次推送拖过宿主的 RPC 上限：超时即跳过，其余图照常发出。"""
    client = FakeClient(
        payloads={"https://example.com/fast.png": PNG},
        delays={"https://example.com/slow.jpg": 5.0},
    )
    downloader = MediaDownloader(client, max_images=3, max_bytes=1024, timeout_seconds=0.2)

    started = time.monotonic()
    bundle = await downloader.collect(
        [_entry(content_html=('<img src="https://example.com/fast.png"><img src="https://example.com/slow.jpg">'))]
    )
    elapsed = time.monotonic() - started

    assert len(bundle.images) == 1
    assert bundle.skipped == 1
    assert bundle.skip_reasons == {SKIP_TIMEOUT: 1}, "超时要能被单独记下来，日志和回复才说得清原因"
    assert elapsed < 1.5
    assert all(call["timeout"] is not None for call in client.calls)


@pytest.mark.asyncio
async def test_time_budget_caps_total_wait() -> None:
    """命令路径要留在宿主的 60 秒调用上限内：传入的整批预算压过配置里的默认值。"""
    client = FakeClient(
        payloads={"https://example.com/a.png": PNG, "https://example.com/b.png": PNG},
        delays={"https://example.com/a.png": 3.0, "https://example.com/b.png": 3.0},
    )
    downloader = MediaDownloader(client, max_images=2, max_bytes=1024, timeout_seconds=30)

    started = time.monotonic()
    bundle = await downloader.collect(
        [
            _entry(
                content_html=('<img src="https://example.com/a.png"><img src="https://example.com/b.png">'),
            )
        ],
        time_budget=0.3,
    )
    elapsed = time.monotonic() - started

    assert bundle.images == []
    assert bundle.skip_reasons == {SKIP_TIMEOUT: 2}
    assert elapsed < 1.5, "整批预算必须真的收窄，而不是等满配置里的 45 秒"


@pytest.mark.asyncio
async def test_undecodable_payload_is_reported_separately() -> None:
    """content-type 说自己是图、字节却解不开时，原因要写「无法解码」而不是「压不进预算」。"""
    client = FakeClient(
        headers={"content-type": "image/jpeg"},
        payloads={"https://example.com/broken.jpg": b"<html>challenge</html>" + b"0" * 2048},
    )
    downloader = MediaDownloader(client, max_images=1, max_bytes=1024 * 1024)

    bundle = await downloader.collect(
        [_entry(content_html='<img src="https://example.com/broken.jpg">')],
        image_budget_bytes=1024,
    )

    assert bundle.images == []
    assert bundle.skip_reasons == {SKIP_UNDECODABLE: 1}
    assert "无法解码" in bundle.describe_skips()


def test_media_budget_constants_fit_host_limits() -> None:
    """预算常量本身也要受 assert：把它们改成不设限的值会立刻失败。"""
    from rsshub.infrastructure.media import downloader as module

    assert 5 <= module._IMAGE_TIMEOUT <= 30, "默认单图超时要落在一张 10 MB 原图能下完的量级"
    assert 1 < module._BATCH_TO_IMAGE_RATIO <= 2, "整批只比单图略宽"
    assert module._IMAGE_TIMEOUT * module._BATCH_TO_IMAGE_RATIO < 60, "整批预算必须留在宿主 60 秒 RPC 上限以内"
    assert 1 <= module._MAX_PARALLEL <= 5
    assert module._SEND_BUDGET_BYTES * 4 // 3 < 16 * 1024 * 1024, "base64 膨胀后必须仍小于宿主 16 MiB 单帧上限"


@pytest.mark.asyncio
async def test_oversized_image_is_compressed_before_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """下载门槛调高后，超过通道预算的原图要先压小再发，否则整条消息发不出去。"""
    import io
    import os

    from PIL import Image

    from rsshub.infrastructure.media import downloader as module

    monkeypatch.setattr(module, "_SEND_BUDGET_BYTES", 200_000)
    # 随机像素保证 PNG 压不动，用来模拟真实站点的大图。
    image = Image.frombytes("RGB", (1200, 1200), os.urandom(1200 * 1200 * 3))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    original = buffer.getvalue()
    assert len(original) > 200_000

    downloader = MediaDownloader(
        FakeClient(payloads={"https://example.com/big.png": original}),
        max_images=1,
        max_bytes=50 * 1024 * 1024,
    )
    bundle = await downloader.collect([_entry(content_html='<img src="https://example.com/big.png">')])

    assert len(bundle.images) == 1
    sent = base64.b64decode(bundle.images[0])
    assert len(sent) <= 200_000
    assert sent.startswith(b"\xff\xd8"), "压缩产物应是 JPEG"


@pytest.fixture
def store(tmp_path: object) -> Store:
    return Store.open(tmp_path / "rsshub.db")  # type: ignore[operator]


def _db_fixtures(store: Store) -> tuple[Feed, Subscription]:
    store.repos.users.get_or_create("u1")
    feed = store.repos.feeds.save(Feed(link="https://example.com/feed.xml", title="示例源"))
    sub = store.repos.subs.save(Subscription(user_id="u1", feed_id=feed.id, target_session="s1"))
    return feed, sub


@pytest.mark.asyncio
async def test_single_image_uses_hybrid(store: Store, tmp_path: object) -> None:
    client = FakeClient(payloads={"https://example.com/a.png": PNG})
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    feed, sub = _db_fixtures(store)
    dispatcher = Dispatcher(ctx, store, make_config(), client)

    await dispatcher.push(
        sub=sub,
        feed=feed,
        entries=[_entry(content_html='<img src="https://example.com/a.png">')],
        settings=_settings(),
        stream_id="s1",
    )

    assert ctx.send.hybrid_calls
    kinds = [segment["type"] for segment in ctx.send.hybrid_calls[0]["segments"]]
    assert kinds == ["text", "image"]
    assert ctx.send.forward_calls == []


@pytest.mark.asyncio
async def test_multiple_images_use_forward(store: Store, tmp_path: object) -> None:
    payloads = {"https://example.com/a.png": PNG, "https://example.com/b.png": PNG}
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    feed, sub = _db_fixtures(store)
    dispatcher = Dispatcher(ctx, store, make_config(), FakeClient(payloads=payloads))

    await dispatcher.push(
        sub=sub,
        feed=feed,
        entries=[_entry(content_html='<img src="https://example.com/a.png"><img src="https://example.com/b.png">')],
        settings=_settings(),
        stream_id="s1",
    )

    assert ctx.send.forward_calls
    content = ctx.send.forward_calls[0]["messages"][0]["content"]
    assert [segment["type"] for segment in content] == ["text", "image", "image"]


@pytest.mark.asyncio
async def test_table_rendered_when_enabled(store: Store, tmp_path: object) -> None:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    feed, sub = _db_fixtures(store)
    dispatcher = Dispatcher(ctx, store, make_config(), FakeClient())

    await dispatcher.push(
        sub=sub,
        feed=feed,
        entries=[_entry(content_html="<table><tr><td>1</td></tr></table>")],
        settings=_settings(),
        stream_id="s1",
    )

    assert ctx.render_calls, "含表格的正文应走 render.html2png"
    assert ctx.send.hybrid_calls
    images = [s for s in ctx.send.hybrid_calls[0]["segments"] if s["type"] == "image"]
    assert images and images[0]["content"] == ctx.render_payload


@pytest.mark.asyncio
async def test_media_disabled_sends_plain_text(store: Store, tmp_path: object) -> None:
    client = FakeClient(payloads={"https://example.com/a.png": PNG})
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    feed, sub = _db_fixtures(store)
    dispatcher = Dispatcher(ctx, store, make_config(), client)

    await dispatcher.push(
        sub=sub,
        feed=feed,
        entries=[_entry(content_html='<img src="https://example.com/a.png">')],
        settings=_settings(display_media=-1),
        stream_id="s1",
    )

    assert ctx.send.sent
    assert ctx.send.hybrid_calls == []


MP3 = b"ID3" + b"0" * 32


@pytest.mark.asyncio
async def test_file_attachments_upload_and_send(tmp_path: object) -> None:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    client = FakeClient(payloads={"https://example.com/ep.mp3": MP3})
    attachments = FileAttachments(ctx, client, max_files=2, max_bytes=1024)

    sent = await attachments.deliver(["https://example.com/ep.mp3"], tmp_path / "media")  # type: ignore[operator]

    assert sent == ["https://example.com/ep.mp3"]
    assert ctx.files.uploads[0]["mime_type"] == "audio/mpeg"
    assert ctx.files.sent == [f"file://{ctx.files.uploads[0]['name']}"]
    assert not list((tmp_path / "media").iterdir())  # type: ignore[operator]


@pytest.mark.asyncio
async def test_file_attachments_without_scope_returns_zero(tmp_path: object) -> None:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    ctx.files.fail_with = PermissionError("文件凭证无效或已过期")
    client = FakeClient(payloads={"https://example.com/ep.mp3": MP3})
    attachments = FileAttachments(ctx, client, max_files=2, max_bytes=1024)

    sent = await attachments.deliver(["https://example.com/ep.mp3"], tmp_path / "media")  # type: ignore[operator]

    assert sent == []
    assert ctx.files.sent == []
    assert not list((tmp_path / "media").iterdir())  # type: ignore[operator]


@pytest.mark.asyncio
async def test_file_attachments_respect_limits(tmp_path: object) -> None:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    client = FakeClient(
        payloads={
            "https://example.com/a.mp3": MP3,
            "https://example.com/b.mp3": MP3,
            "https://example.com/huge.mp4": b"0" * 2048,
        }
    )
    attachments = FileAttachments(ctx, client, max_files=1, max_bytes=1024)

    sent = await attachments.deliver(
        [
            "https://example.com/a.mp3",
            "https://example.com/b.mp3",
            "https://example.com/huge.mp4",
        ],
        tmp_path / "media",  # type: ignore[operator]
    )

    assert sent == ["https://example.com/a.mp3"]
    assert len(ctx.files.uploads) == 1, "达到文件数上限后不再上传，超体积文件同样被挡下"


@pytest.mark.asyncio
async def test_file_attachments_skip_oversized_file(tmp_path: object) -> None:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    client = FakeClient(
        payloads={
            "https://example.com/a.mp3": MP3,
            "https://example.com/huge.mp4": b"0" * 4096,
        }
    )
    attachments = FileAttachments(ctx, client, max_files=5, max_bytes=1024)

    sent = await attachments.deliver(
        ["https://example.com/a.mp3", "https://example.com/huge.mp4"],
        tmp_path / "media",  # type: ignore[operator]
    )

    assert sent == ["https://example.com/a.mp3"], "超体积文件不发送"
    assert len(ctx.files.uploads) == 1, "超体积文件不该上传"
