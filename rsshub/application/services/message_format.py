"""推送文本编排：直推模板与人格化参考材料。"""

from __future__ import annotations

from typing import Any

from ...domain.entry import Entry
from ...domain.settings import EffectiveSettings


def _entry_block(entry: Entry, settings: EffectiveSettings) -> str:
    title = entry.title or "无标题"
    lines: list[str] = []
    if settings.display_title != 2:
        lines.append(title)
    summary = entry.summary
    if settings.length_limit > 0 and len(summary) > settings.length_limit:
        summary = summary[: settings.length_limit].rstrip() + "…"
    if summary and settings.display_entry_tags != 2:
        lines.append(summary)
    if settings.display_author == 1 and entry.author:
        lines.append(f"作者：{entry.author}")
    if entry.link:
        lines.append(entry.link)
    return "\n".join(lines)


def build_direct_text(feed_title: str, entries: list[Entry], settings: EffectiveSettings) -> str:
    """直推模板：标题 + 摘要 + 链接，多条目分行。"""
    blocks = [_entry_block(entry, settings) for entry in entries]
    header = f"【{feed_title}】更新" if feed_title else "订阅更新"
    return header + "\n\n" + "\n\n".join(blocks)


def build_entry_text(feed_title: str, entry: Entry, settings: EffectiveSettings) -> str:
    """单条目直推文本：重试与推送历史按条目存这一份，不存整批。"""
    header = f"【{feed_title}】更新" if feed_title else "订阅更新"
    return header + "\n\n" + _entry_block(entry, settings)


def build_reference(feed_title: str, entries: list[Entry], max_chars: int) -> str:
    """人格化生成的写作材料：保留标题、摘要、正文片段、链接。"""
    parts: list[str] = []
    if feed_title:
        parts.append(f"订阅源：{feed_title}")
    for entry in entries:
        block = [f"标题：{entry.title or '无标题'}"]
        if entry.summary:
            block.append(f"摘要：{entry.summary}")
        if entry.content:
            block.append(f"正文节选：{entry.content[:1500]}")
        if entry.link:
            block.append(f"链接：{entry.link}")
        parts.append("\n".join(block))
    text = "\n\n".join(parts)
    return text[:max_chars] if max_chars > 0 else text


def segments_from_text(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "content": text}]
