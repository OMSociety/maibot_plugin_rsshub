"""Feed 解析：feedparser → Entry，并合成原始 item XML。

条目 raw_xml 是 ai_transform(xml scope) 的输入，源插件尽力回填原文 item；
此处用固定字段合成等价 XML（结构与 RSS item 一致），供 handler 使用。
"""

from __future__ import annotations

import html
import re
import time
from datetime import UTC, datetime
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup

from ...domain.entry import Enclosure, Entry, FeedDocument
from ...errors import FeedFetchError

_TRACKING_PARAMS = frozenset({"gclid", "fbclid", "spm", "ref", "ref_src", "mc_cid", "mc_eid", "igshid", "yclid"})
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return html.unescape(_TAG_RE.sub(" ", value)).strip()


def _strip_tracking(url: str) -> str:
    from ...infrastructure.fetcher.url_guard import strip_tracking_params

    return strip_tracking_params(url, _TRACKING_PARAMS)


def _to_datetime(struct_time: time.struct_time | None) -> datetime | None:
    if not struct_time:
        return None
    return datetime(*struct_time[:6], tzinfo=UTC)


def _entry_xml(entry: Entry) -> str:
    """合成 item XML（字段与 RSS item 对齐，足以让 handler 读写）。"""
    parts = ["<item>"]
    if entry.title:
        parts.append(f"<title><![CDATA[{entry.title}]]></title>")
    if entry.link:
        parts.append(f"<link>{html.escape(entry.link)}</link>")
    if entry.guid:
        parts.append(f"<guid>{html.escape(entry.guid)}</guid>")
    if entry.author:
        parts.append(f"<author>{html.escape(entry.author)}</author>")
    if entry.published:
        parts.append(f"<pubDate>{entry.published.isoformat()}</pubDate>")
    for tag in entry.tags:
        parts.append(f"<category>{html.escape(tag)}</category>")
    if entry.summary:
        parts.append(f"<description><![CDATA[{entry.summary}]]></description>")
    if entry.content:
        parts.append(f"<content:encoded><![CDATA[{entry.content}]]></content:encoded>")
    parts.append("</item>")
    return "".join(parts)


def parse_feed(body: bytes, feed_url: str) -> FeedDocument:
    """解析 RSS/Atom/JSON Feed。"""
    parsed = feedparser.parse(body)
    if parsed.bozo and not parsed.entries:
        raise FeedFetchError(f"无法解析 Feed 内容: {feed_url}")

    feed_title = _clean_text(parsed.feed.get("title", ""))
    feed_link = parsed.feed.get("link", feed_url)

    entries: list[Entry] = []
    for raw in parsed.entries:
        link = raw.get("link") or raw.get("guid") or ""
        if link and not link.startswith(("http://", "https://")):
            link = urljoin(feed_link or feed_url, link)
        link = _strip_tracking(link)

        content = ""
        if raw.get("content"):
            content = raw["content"][0].get("value", "") if isinstance(raw["content"], list) else str(raw["content"])
        if not content:
            content = raw.get("content_encoded") or raw.get("summary") or ""
        summary = strip_html(raw.get("summary") or raw.get("description") or "")
        content = strip_html(content) or summary

        published = _to_datetime(raw.get("published_parsed")) or _to_datetime(raw.get("updated_parsed"))
        updated = _to_datetime(raw.get("updated_parsed"))

        enclosures: list[Enclosure] = []
        for item in raw.get("enclosures", []) or []:
            url = item.get("href") or item.get("url") or ""
            if url:
                try:
                    length = int(item.get("length") or 0)
                except (TypeError, ValueError):
                    length = 0
                enclosures.append(Enclosure(url=url, length=length, type=item.get("type") or ""))
        for media in raw.get("media_content", []) or []:
            url = media.get("url") or media.get("href") or ""
            if url and all(e.url != url for e in enclosures):
                enclosures.append(Enclosure(url=url, type=media.get("type") or "", length=0))

        entry = Entry(
            title=_clean_text(raw.get("title", "")),
            link=link,
            author=_clean_text(raw.get("author", "")),
            content=content,
            summary=summary,
            guid=raw.get("guid", "") or "",
            entry_id=str(raw.get("id", "") or ""),
            tags=[tag.get("term", "") for tag in raw.get("tags", []) or [] if tag.get("term")],
            enclosures=enclosures,
            published=published,
            updated=updated,
        )
        entry.raw_xml = _entry_xml(entry)
        entries.append(entry)

    return FeedDocument(url=feed_url, title=feed_title, entries=entries, raw_body=body.decode("utf-8", "replace"))


def strip_html(text: str) -> str:
    """去 HTML 标签，保留纯文本（摘要用）。"""
    if not text:
        return ""
    soup = BeautifulSoup(text, "lxml")
    plain = soup.get_text("\n")
    return re.sub(r"\n{3,}", "\n\n", plain).strip()
