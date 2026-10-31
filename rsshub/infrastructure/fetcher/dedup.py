"""条目去重哈希。

与源插件同构：条目级稳定哈希（v3 前缀）+ 内容哈希；
feed.entry_hashes 保存历史哈希，任一命中即视为旧条目。
"""

from __future__ import annotations

import hashlib

from ...domain.entry import Entry

HASH_PREFIX = "v3"
HASH_HISTORY_MIN = 200
HASH_HISTORY_MULTIPLIER = 2
HASH_HISTORY_HARD_LIMIT = 5000


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def entry_hash_set(entry: Entry) -> list[str]:
    """返回该条目的全部判重哈希。"""
    ident = entry.entry_id or entry.link or entry.title or entry.summary[:256]
    sid_payload = f"{HASH_PREFIX}|id={_normalize(ident)}"
    content_payload = (
        f"{HASH_PREFIX}|title={_normalize(entry.title)}"
        f"|link={_normalize(entry.link)}"
        f"|summary={_normalize(entry.summary)[:512]}"
    )
    return [f"sid:{_sha256(sid_payload)}", f"content:{_sha256(content_payload)}"]


def is_known_entry(entry: Entry, known_hashes: set[str]) -> bool:
    return any(item in known_hashes for item in entry_hash_set(entry))


def merge_hashes(known: list[str], new_hashes: list[str]) -> list[str]:
    """合并历史哈希并按上限裁剪（保留最新的在后）。"""
    merged = list(known)
    seen = set(known)
    for item in new_hashes:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    limit = max(HASH_HISTORY_MIN, len(new_hashes) * HASH_HISTORY_MULTIPLIER)
    limit = min(limit, HASH_HISTORY_HARD_LIMIT)
    return merged[-limit:] if len(merged) > limit else merged
