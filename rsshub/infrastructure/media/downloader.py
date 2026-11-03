"""富媒体收集与下载：正文图、enclosure、音视频文件与表格渲染。

宿主对文件发送有作用域限制：``ctx.files.*`` 只在模型工具调用期间可用（宿主按调用会话
签发 1 小时文件凭证），后台调度器的推送拿不到凭证。因此音视频的呈现分两条路径：

- 工具调用内（``rss_push_xml_entry``）：下载后走 ``ctx.files.upload`` + ``ctx.files.send``
  以原文件送达当前会话；
- 后台定时推送：没有文件凭证，附一行音视频链接，由用户点开原始地址。

没有作用域时调用 ``ctx.files.*`` 会抛 ``PermissionError``，由 :class:`FileAttachments`
捕获后返回 0，调用方据此退回链接行。
"""

from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from bs4 import BeautifulSoup

from ...domain.entry import Enclosure, Entry
from ...errors import FeedFetchError, FeedTimeoutError
from ..fetcher.client import HttpClient
from .compressor import compress_image

# 单张图与整批图的下载上限：图片站（如 pixiv 原图）单张常达数 MB 到十几 MB，
# 串行下载会把命令调用拖过宿主的 60 秒 RPC 超时。超时即跳过该图，推送继续。
# 20 秒按 0.6 MB/s 的慢速链路也能下完 10 MB 左右的原图；整批给 1.5 倍余量。
_IMAGE_TIMEOUT = 20.0
_BATCH_TO_IMAGE_RATIO = 1.5
_MAX_PARALLEL = 3

# 宿主插件通道的单帧上限是 16 MiB，图片按 base64 传输会膨胀约 1/3，
# 所以原图超过 8 MiB 时先压到这个预算内再发送，否则整条消息发不出去。
_SEND_BUDGET_BYTES = 8 * 1024 * 1024

# 跳过原因：日志、注入提示与 /测试推送 的回复都按这几个词汇报，模型据此回答用户。
SKIP_TIMEOUT = "下载超时"
SKIP_NOT_IMAGE = "返回的不是图片"
SKIP_TOO_LARGE = "超过单张大小上限"
SKIP_HTTP = "站点没有正常返回"
SKIP_COMPRESS = "压不进大小预算"
SKIP_LIMIT = "超出张数上限"

_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/avif")
_VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".m3u8")
_AUDIO_SUFFIXES = (".mp3", ".m4a", ".ogg", ".wav", ".flac")
_MIME_BY_SUFFIX = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
}


@dataclass
class MediaBundle:
    """一次推送可附带的媒体素材。"""

    images: list[str] = field(default_factory=list)  # base64 编码
    audio_video_links: list[str] = field(default_factory=list)
    skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)

    def note_skip(self, reason: str) -> None:
        self.skipped += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1

    def describe_skips(self) -> str:
        """把跳过原因拼成一句人话，例如「3 张（下载超时 3）」。"""
        if not self.skipped:
            return ""
        detail = "、".join(f"{reason} {count}" for reason, count in self.skip_reasons.items())
        return f"{self.skipped} 张（{detail}）" if detail else f"{self.skipped} 张"


def _is_image(enclosure: Enclosure) -> bool:
    kind = (enclosure.type or "").lower()
    if kind.startswith("image/"):
        return True
    return any(
        enclosure.url.lower().split("?")[0].endswith(suffix) for suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp")
    )


def _is_audio_video(enclosure: Enclosure) -> bool:
    kind = (enclosure.type or "").lower()
    if kind.startswith(("video/", "audio/")):
        return True
    url = enclosure.url.lower().split("?")[0]
    return any(url.endswith(suffix) for suffix in (*_VIDEO_SUFFIXES, *_AUDIO_SUFFIXES))


def _content_image_urls(entry: Entry) -> list[str]:
    if not entry.content_html:
        return []
    soup = BeautifulSoup(entry.content_html, "lxml")
    urls: list[str] = []
    for tag in soup.find_all("img"):
        raw = tag.get("src") or tag.get("data-src") or ""
        if not raw:
            continue
        for candidate in re.split(r"[,\s]+", raw):
            url = candidate.strip().split(" ")[0]
            if not url or url.startswith("data:"):
                continue
            urls.append(urljoin(entry.link or "", url))
    return urls


def _av_tag_links(entry: Entry) -> list[str]:
    """正文里 <video>/<audio>/<source> 的直接链接。"""
    if not entry.content_html:
        return []
    soup = BeautifulSoup(entry.content_html, "lxml")
    links: list[str] = []
    for tag in soup.find_all(["video", "audio", "source"]):
        raw = tag.get("src") or ""
        if raw:
            links.append(urljoin(entry.link or "", raw))
    return links


def collect_media_candidates(entry: Entry) -> tuple[list[str], list[str]]:
    """返回 (图片 URL 列表, 音视频链接列表)。"""
    images = [enclosure.url for enclosure in entry.enclosures if _is_image(enclosure)]
    images.extend(_content_image_urls(entry))
    av = [enclosure.url for enclosure in entry.enclosures if _is_audio_video(enclosure)]
    av.extend(_av_tag_links(entry))

    deduped_images: list[str] = []
    seen: set[str] = set()
    for url in images:
        if url and url not in seen:
            seen.add(url)
            deduped_images.append(url)

    deduped_av: list[str] = []
    for url in av:
        if url and url not in seen:
            seen.add(url)
            deduped_av.append(url)
    return deduped_images, deduped_av


class MediaDownloader:
    """下载正文图片并按上限裁剪；失败图片跳过而不中断推送。"""

    def __init__(
        self,
        client: HttpClient,
        *,
        max_images: int,
        max_bytes: int,
        timeout_seconds: float | None = None,
    ) -> None:
        self._client = client
        self._max_images = max(0, max_images)
        self._max_bytes = max(0, max_bytes)
        # 单图超时来自配置（media.image_timeout_seconds）：图大的源自己调，整批按比例放宽。
        self._timeout = float(timeout_seconds) if timeout_seconds else _IMAGE_TIMEOUT
        self._budget = self._timeout * _BATCH_TO_IMAGE_RATIO

    async def collect(self, entries: list[Entry], *, image_budget_bytes: int | None = None) -> MediaBundle:
        """收集一批条目的图与音视频链接。

        ``image_budget_bytes`` 给出时按该预算压图（注入模式：图要留在聊天历史里），
        压不下去的图按跳过处理；不传则保留原图（直接发送的推送）。
        """
        bundle = MediaBundle()
        candidates: list[str] = []
        for entry in entries:
            images, av = collect_media_candidates(entry)
            bundle.audio_video_links.extend(url for url in av if url not in bundle.audio_video_links)
            for url in images:
                if url not in candidates:
                    candidates.append(url)
        if self._max_images == 0 or not candidates:
            # max_images=0 表示不带图：音视频链接行照旧给出，图片一张不取。
            return bundle
        payloads = await self._download_batch(candidates[: self._max_images * 2], image_budget_bytes)
        for payload, reason in payloads:
            if payload is None:
                bundle.note_skip(reason or SKIP_HTTP)
            elif len(bundle.images) < self._max_images:
                bundle.images.append(payload)
            else:
                # 下载成功但因张数上限被丢掉的图，同样计入 skipped，避免日志口径失真
                bundle.note_skip(SKIP_LIMIT)
        return bundle

    async def _download_batch(self, urls: list[str], budget: int | None = None) -> list[tuple[str | None, str]]:
        """并发下载，超过整批预算就取消剩余任务；返回与 urls 等长的 (载荷, 跳过原因)。"""
        if not urls:
            return []
        gate = asyncio.Semaphore(_MAX_PARALLEL)

        async def one(url: str) -> tuple[str | None, str]:
            async with gate:
                return await self._fetch_image(url, budget)

        tasks = {asyncio.create_task(one(url)): index for index, url in enumerate(urls)}
        done, pending = await asyncio.wait(tasks, timeout=self._budget)
        for task in pending:
            task.cancel()
        # 整批预算用尽时被取消的任务统一记作下载超时
        results: list[tuple[str | None, str]] = [(None, SKIP_TIMEOUT)] * len(urls)
        for task in done:
            if task.cancelled() or task.exception() is not None:
                continue
            results[tasks[task]] = task.result()
        return results

    async def _fetch_image(self, url: str, budget: int | None = None) -> tuple[str | None, str]:
        try:
            result = await asyncio.wait_for(self._client.fetch(url, timeout=self._timeout), self._timeout + 1)
        except (FeedTimeoutError, TimeoutError):
            return None, SKIP_TIMEOUT
        except FeedFetchError:
            return None, SKIP_HTTP
        if result.status != 200 or not result.content:
            return None, SKIP_HTTP
        if len(result.content) > self._max_bytes:
            return None, SKIP_TOO_LARGE
        if not self._looks_like_image(result.content, result.headers):
            return None, SKIP_NOT_IMAGE
        payload = result.content
        if budget is not None:
            compressed = compress_image(payload, max_bytes=budget)
            if compressed is None:
                return None, SKIP_COMPRESS
            payload = compressed
        elif len(payload) > _SEND_BUDGET_BYTES:
            compressed = compress_image(payload, max_bytes=_SEND_BUDGET_BYTES)
            if compressed is None:
                return None, SKIP_COMPRESS
            payload = compressed
        return base64.b64encode(payload).decode("ascii"), ""

    @staticmethod
    def _looks_like_image(payload: bytes, headers: dict[str, str]) -> bool:
        content_type = (headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type.startswith("image/"):
            return content_type in _IMAGE_TYPES or content_type.startswith("image/")
        head = payload[:12]
        return head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"BM"))


def av_link_card_text(links: list[str], *, title: str = "") -> str:
    """无文件凭证时的音视频链接行。"""
    if not links:
        return ""
    header = f"{title}\n" if title else ""
    return header + "\n".join(f"音视频：{link}" for link in links)


@dataclass
class TransferFile:
    """已落到临时目录、等待上传的附件。"""

    path: Path
    name: str
    mime: str


def _guess_file_type(url: str, headers: dict[str, str]) -> tuple[str, str]:
    content_type = (headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type.startswith(("audio/", "video/")):
        suffix = Path(urlparse(url).path).suffix.lower()
        return suffix, content_type
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    return suffix, _MIME_BY_SUFFIX.get(suffix, "application/octet-stream")


class FileAttachments:
    """把音视频下载到临时目录，再经宿主文件服务送达当前工具调用的会话。

    逐条串行：宿主一次只发一个文件，且上传凭证与调用会话绑定。任何一条失败不阻断其余
    条目，失败数由调用方换算成链接行。上限同时约束文件数与单文件体积，避免长视频把
    工具调用拖到超时。
    """

    def __init__(self, ctx: object, client: HttpClient, *, max_files: int, max_bytes: int) -> None:
        self._ctx = ctx
        self._client = client
        self._max_files = max(0, max_files)
        self._max_bytes = max(0, max_bytes)

    async def deliver(self, links: list[str], directory: Path) -> list[str]:
        """返回成功送达的文件链接；失败或超出上限的条目由调用方转成链接行。

        返回的是具体链接而不是数量：中间某条失败时，调用方仍能准确知道哪些还没送出。
        """
        if not links or self._max_files == 0 or self._max_bytes == 0:
            return []
        directory.mkdir(parents=True, exist_ok=True)
        sent: list[str] = []
        for url in links[: self._max_files]:
            transfer = await self._download(url, directory)
            if transfer is None:
                continue
            try:
                if await self._send(transfer):
                    sent.append(url)
            finally:
                transfer.path.unlink(missing_ok=True)
        return sent

    async def _download(self, url: str, directory: Path) -> TransferFile | None:
        try:
            result = await self._client.fetch(url)
        except FeedFetchError:
            return None
        if result.status != 200 or not result.content:
            return None
        if len(result.content) > self._max_bytes:
            return None
        suffix, mime = _guess_file_type(result.url or url, result.headers)
        path = directory / f"{uuid4().hex}{suffix}"
        path.write_bytes(result.content)
        return TransferFile(path=path, name=f"{path.stem}{suffix}", mime=mime)

    async def _send(self, transfer: TransferFile) -> bool:
        files = getattr(self._ctx, "files", None)
        if files is None:
            return False
        try:
            uploaded = await files.upload(transfer.path, name=transfer.name, mime_type=transfer.mime)
            file_ref = uploaded.get("file_ref") if isinstance(uploaded, dict) else ""
            if not file_ref:
                self._log(f"RSS 附件上传未返回 file_ref：{transfer.name}")
                return False
            result = await files.send(file_ref)
            return not (isinstance(result, dict) and result.get("error"))
        except Exception as exc:  # 宿主拒绝（无凭证）与传输失败都退回链接行
            self._log(f"RSS 附件发送失败，改用链接：{exc}")
            return False

    def _log(self, message: str) -> None:
        logger = getattr(self._ctx, "logger", None)
        if logger is not None:
            logger.warning(message)
