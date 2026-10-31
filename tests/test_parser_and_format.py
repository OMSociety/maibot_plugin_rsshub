"""解析器与消息格式化测试。"""

from __future__ import annotations

from rsshub.application.services.message_format import build_direct_text, build_reference
from rsshub.domain.settings import EffectiveSettings
from rsshub.infrastructure.fetcher.parser import parse_feed

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>示例源</title>
  <link>https://example.com</link>
  <item>
    <title>第一篇</title>
    <link>https://example.com/posts/1?utm_source=rss</link>
    <guid>post-1</guid>
    <author>作者甲</author>
    <pubDate>Mon, 05 May 2025 10:00:00 GMT</pubDate>
    <description><![CDATA[<p>摘要一</p>]]></description>
  </item>
  <item>
    <title>第二篇</title>
    <link>/relative/2</link>
    <guid>post-2</guid>
    <pubDate>Tue, 06 May 2025 10:00:00 GMT</pubDate>
    <description>摘要二</description>
  </item>
</channel></rss>"""
RSS = RSS_XML.encode("utf-8")


def _settings(mode: str = "direct", length_limit: int = 0) -> EffectiveSettings:
    return EffectiveSettings(
        interval_seconds=600,
        notify=True,
        push_mode=mode,
        length_limit=length_limit,
        display_author=0,
        display_via=0,
        display_title=0,
        display_entry_tags=0,
        style=0,
        display_media=0,
    )


def test_parse_feed_extracts_entries_and_resolves_links() -> None:
    document = parse_feed(RSS, "https://example.com/feed.xml")

    assert document.title == "示例源"
    assert len(document.entries) == 2
    first, second = document.entries
    assert first.guid == "post-1"
    assert "utm_source" not in first.link
    assert first.summary == "摘要一"
    assert first.raw_xml.startswith("<item>")
    assert second.link == "https://example.com/relative/2"
    assert second.published is not None and second.published.year == 2025


def test_latest_entries_orders_by_time_desc() -> None:
    document = parse_feed(RSS, "https://example.com/feed.xml")

    latest = document.latest_entries(1)

    assert [entry.title for entry in latest] == ["第二篇"]


def test_direct_text_contains_title_and_links() -> None:
    document = parse_feed(RSS, "https://example.com/feed.xml")

    text = build_direct_text("示例源", document.entries, _settings())

    assert "示例源" in text
    assert "第一篇" in text
    assert "https://example.com/posts/1" in text


def test_reference_is_truncated_to_limit() -> None:
    document = parse_feed(RSS, "https://example.com/feed.xml")

    reference = build_reference("示例源", document.entries, 120)

    assert len(reference) <= 120
    assert "示例源" in reference
