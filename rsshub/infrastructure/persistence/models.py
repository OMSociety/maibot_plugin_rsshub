"""数据行模型（轻量 dataclass，不做 ORM）。

字段语义与源插件 astrbot_plugin_rsshub 的实体保持一致；
push_mode 在本移植中取代源插件的 send_mode（int 哨兵）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .database import INHERIT_MODE, INHERIT_VALUE


@dataclass
class User:
    user_id: str
    state: int = 1
    interval_minutes: int = INHERIT_VALUE
    notify: int = INHERIT_VALUE
    push_mode: str = INHERIT_MODE
    handlers_mode: str = INHERIT_MODE
    handlers: list[dict[str, Any]] = field(default_factory=list)
    length_limit: int = INHERIT_VALUE
    display_author: int = INHERIT_VALUE
    display_via: int = INHERIT_VALUE
    display_title: int = INHERIT_VALUE
    display_entry_tags: int = INHERIT_VALUE
    style: int = INHERIT_VALUE
    display_media: int = INHERIT_VALUE
    default_target_session: str | None = None
    needs_binding_notice: int = 0


@dataclass
class Feed:
    link: str
    id: int | None = None
    title: str = ""
    etag: str | None = None
    last_modified: str | None = None
    entry_hashes: list[str] = field(default_factory=list)


@dataclass
class Subscription:
    user_id: str
    feed_id: int
    id: int | None = None
    state: int = 1
    title: str = ""
    tags: str = ""
    target_session: str | None = None
    platform_name: str | None = None
    session_label: str = ""
    interval_minutes: int = INHERIT_VALUE
    next_check_at: str | None = None
    notify: int = INHERIT_VALUE
    push_mode: str = INHERIT_MODE
    handlers_mode: str = INHERIT_MODE
    handlers: list[dict[str, Any]] = field(default_factory=list)
    length_limit: int = INHERIT_VALUE
    display_author: int = INHERIT_VALUE
    display_via: int = INHERIT_VALUE
    display_title: int = INHERIT_VALUE
    display_entry_tags: int = INHERIT_VALUE
    style: int = INHERIT_VALUE
    display_media: int = INHERIT_VALUE


@dataclass
class PushHistory:
    user_id: str
    id: int | None = None
    sub_id: int | None = None
    feed_id: int | None = None
    source_type: str = "feed"
    source_key: str | None = None
    content: str = ""
    raw_xml: str | None = None
    media_urls: list[str] = field(default_factory=list)
    handler_trace: list[dict[str, Any]] = field(default_factory=list)
    entry_title: str = ""
    entry_link: str = ""
    entry_guid: str | None = None
    feed_title: str = ""
    feed_link: str = ""
    platform_name: str | None = None
    target_session: str | None = None
    status: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    fail_reason: str | None = None
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def user_from_row(row: Any) -> User:
    return User(
        user_id=row["user_id"],
        state=row["state"],
        interval_minutes=row["interval_minutes"],
        notify=row["notify"],
        push_mode=row["push_mode"],
        handlers_mode=row["handlers_mode"],
        handlers=_loads(row["handlers"], []),
        length_limit=row["length_limit"],
        display_author=row["display_author"],
        display_via=row["display_via"],
        display_title=row["display_title"],
        display_entry_tags=row["display_entry_tags"],
        style=row["style"],
        display_media=row["display_media"],
        default_target_session=row["default_target_session"],
        needs_binding_notice=row["needs_binding_notice"],
    )


def feed_from_row(row: Any) -> Feed:
    return Feed(
        id=row["id"],
        link=row["link"],
        title=row["title"],
        etag=row["etag"],
        last_modified=row["last_modified"],
        entry_hashes=_loads(row["entry_hashes"], []),
    )


def sub_from_row(row: Any) -> Subscription:
    return Subscription(
        id=row["id"],
        state=row["state"],
        user_id=row["user_id"],
        feed_id=row["feed_id"],
        title=row["title"],
        tags=row["tags"],
        target_session=row["target_session"],
        platform_name=row["platform_name"],
        session_label=row["session_label"] or "",
        interval_minutes=row["interval_minutes"],
        next_check_at=row["next_check_at"],
        notify=row["notify"],
        push_mode=row["push_mode"],
        handlers_mode=row["handlers_mode"],
        handlers=_loads(row["handlers"], []),
        length_limit=row["length_limit"],
        display_author=row["display_author"],
        display_via=row["display_via"],
        display_title=row["display_title"],
        display_entry_tags=row["display_entry_tags"],
        style=row["style"],
        display_media=row["display_media"],
    )


def history_from_row(row: Any) -> PushHistory:
    return PushHistory(
        id=row["id"],
        sub_id=row["sub_id"],
        user_id=row["user_id"],
        feed_id=row["feed_id"],
        source_type=row["source_type"],
        source_key=row["source_key"],
        content=row["content"],
        raw_xml=row["raw_xml"],
        media_urls=_loads(row["media_urls"], []),
        handler_trace=_loads(row["handler_trace"], []),
        entry_title=row["entry_title"],
        entry_link=row["entry_link"],
        entry_guid=row["entry_guid"],
        feed_title=row["feed_title"],
        feed_link=row["feed_link"],
        platform_name=row["platform_name"],
        target_session=row["target_session"],
        status=row["status"],
        retry_count=row["retry_count"],
        max_retries=row["max_retries"],
        fail_reason=row["fail_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )
