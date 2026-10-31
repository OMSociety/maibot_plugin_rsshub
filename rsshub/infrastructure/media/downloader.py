"""富媒体收集与下载：正文图、enclosure、表格渲染，以及音视频链接卡。

能力边界（SDK 2.16.0）：宿主只有文本、图片、图文混排与合并转发四条发送通道，
没有视频/音频/文件通道。因此音视频一律降级为链接卡文本。

REMOVAL：SDK ≥ 2.17.0 提供 ``ctx.files.send`` 后，把 ``_audio_video_links`` 产出的
链接卡换成文件发送，并移除本模块中 ``link_card`` 相关分支。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ...domain.entry import Enclosure, Entry
from ...errors import FeedFetchError
from ..fetcher.client import HttpClient

_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/avif")
_VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".m3u8")
_AUDIO_SUFFIXES = (".mp3", ".m4a", ".ogg", ".wav", ".flac")


@dataclass
class MediaBundle:
    """一次推送可附带的媒体素材。"""

    images: list[str] = field(default_factory=list)  # base64 编码
    audio_video_links: list[str] = field(default_factory=list)
    skipped: int = 0


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

    def __init__(self, client: HttpClient, *, max_images: int, max_bytes: int) -> None:
        self._client = client
        self._max_images = max(0, max_images)
        self._max_bytes = max(0, max_bytes)

    async def collect(self, entries: list[Entry]) -> MediaBundle:
        bundle = MediaBundle()
        if self._max_images == 0:
            return bundle
        for entry in entries:
            images, av = collect_media_candidates(entry)
            bundle.audio_video_links.extend(url for url in av if url not in bundle.audio_video_links)
            for url in images:
                if len(bundle.images) >= self._max_images:
                    return bundle
                payload = await self._fetch_image(url)
                if payload is None:
                    bundle.skipped += 1
                    continue
                bundle.images.append(payload)
        return bundle

    async def _fetch_image(self, url: str) -> str | None:
        try:
            result = await self._client.fetch(url)
        except FeedFetchError:
            return None
        if result.status != 200 or not result.content:
            return None
        if len(result.content) > self._max_bytes:
            return None
        if not self._looks_like_image(result.content, result.headers):
            return None
        return base64.b64encode(result.content).decode("ascii")

    @staticmethod
    def _looks_like_image(payload: bytes, headers: dict[str, str]) -> bool:
        content_type = (headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type.startswith("image/"):
            return content_type in _IMAGE_TYPES or content_type.startswith("image/")
        head = payload[:12]
        return head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"BM"))


def av_link_card_text(links: list[str], *, title: str = "") -> str:
    """音视频降级为链接卡文本。

    REMOVAL：SDK ≥ 2.17.0 的 ``ctx.files.send`` 可用后，改为文件发送，删除本函数。
    """
    if not links:
        return ""
    header = f"{title}\n" if title else ""
    return header + "\n".join(f"音视频：{link}" for link in links)
