"""Feed 条目模型。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Enclosure:
    url: str
    length: int = 0
    type: str = ""


@dataclass
class Entry:
    title: str = ""
    link: str = ""
    author: str = ""
    content: str = ""
    summary: str = ""
    guid: str = ""
    entry_id: str = ""
    raw_xml: str = ""
    tags: list[str] = field(default_factory=list)
    enclosures: list[Enclosure] = field(default_factory=list)
    published: datetime | None = None
    updated: datetime | None = None

    def dedup_key(self) -> str:
        """条目稳定标识：guid → link → entry_id → 标题+发布时间。"""
        for candidate in (self.guid, self.link, self.entry_id):
            if candidate and candidate.strip():
                return candidate.strip()
        stamp = self.published.isoformat() if self.published else ""
        return f"{self.title}|{stamp}"

    def dedup_hash(self) -> str:
        return hashlib.sha256(self.dedup_key().encode("utf-8")).hexdigest()[:16]

    def text_content(self) -> str:
        return "\n".join(part for part in (self.title, self.content or self.summary) if part)


@dataclass
class FeedDocument:
    """一次抓取的解析结果。"""

    url: str
    title: str = ""
    entries: list[Entry] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    raw_body: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def latest_entries(self, limit: int) -> list[Entry]:
        """按发布时间取最近的若干条目；无时间的条目排在有时间的之后。"""
        if limit <= 0:
            return []

        def _stamp(entry: Entry) -> float:
            moment = entry.published or entry.updated
            return moment.timestamp() if moment else 0.0

        return sorted(self.entries, key=_stamp, reverse=True)[:limit]
