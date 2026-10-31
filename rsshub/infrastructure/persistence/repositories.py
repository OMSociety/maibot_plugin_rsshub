"""仓库层：所有 SQL 只出现在这里。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from .database import Database, utcnow
from .models import (
    Feed,
    PushHistory,
    Subscription,
    User,
    feed_from_row,
    history_from_row,
    sub_from_row,
    user_from_row,
)

_JSON_FIELDS = {"handlers", "entry_hashes", "media_urls", "handler_trace"}

# 可更新列白名单：UPDATE 的列名只能来自这里，杜绝把外部输入拼进 SQL。
_USER_COLUMNS = frozenset(
    {
        "state",
        "interval_minutes",
        "notify",
        "push_mode",
        "handlers_mode",
        "handlers",
        "length_limit",
        "display_author",
        "display_via",
        "display_title",
        "display_entry_tags",
        "style",
        "display_media",
        "default_target_session",
        "needs_binding_notice",
    }
)
_SUB_COLUMNS = frozenset(
    {
        "state",
        "title",
        "tags",
        "target_session",
        "platform_name",
        "interval_minutes",
        "next_check_at",
        "notify",
        "push_mode",
        "handlers_mode",
        "handlers",
        "length_limit",
        "display_author",
        "display_via",
        "display_title",
        "display_entry_tags",
        "style",
        "display_media",
    }
)
_HISTORY_COLUMNS = frozenset(
    {
        "status",
        "retry_count",
        "max_retries",
        "fail_reason",
        "completed_at",
        "content",
        "media_urls",
        "handler_trace",
        "raw_xml",
        "entry_title",
        "entry_link",
        "entry_guid",
        "feed_title",
        "feed_link",
    }
)


def _update_clause(fields: dict, allowed: frozenset[str]) -> tuple[str, tuple]:
    """把待更新字段拼成 SET 子句；列名不在白名单内直接报错。"""
    unknown = [name for name in fields if name not in allowed]
    if unknown:
        raise ValueError(f"不可更新的列: {', '.join(sorted(unknown))}")
    columns = ", ".join(f"{name}=?" for name in fields)
    params = tuple(_dump(value) if name in _JSON_FIELDS else value for name, value in fields.items())
    return columns, params


def _dump(value: object) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _now() -> datetime:
    return datetime.now(UTC)


class UserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, user_id: str) -> User | None:
        row = self._db.query_one("SELECT * FROM rsshub_users WHERE user_id=?", (user_id,))
        return user_from_row(row) if row else None

    def get_or_create(self, user_id: str) -> User:
        user = self.get(user_id)
        if user is None:
            now = utcnow()
            self._db.execute(
                "INSERT INTO rsshub_users(user_id, created_at, updated_at) VALUES (?,?,?)",
                (user_id, now, now),
            )
            user = self.get(user_id)
            assert user is not None
        return user

    def update(self, user_id: str, **fields) -> None:
        if not fields:
            return
        columns, params = _update_clause(fields, _USER_COLUMNS)
        self._db.execute(
            f"UPDATE rsshub_users SET {columns}, updated_at=? WHERE user_id=?",  # noqa: S608 - 列名经白名单校验
            (*params, utcnow(), user_id),
        )


class FeedRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get_by_link(self, link: str) -> Feed | None:
        row = self._db.query_one("SELECT * FROM rsshub_feeds WHERE link=?", (link,))
        return feed_from_row(row) if row else None

    def get(self, feed_id: int) -> Feed | None:
        row = self._db.query_one("SELECT * FROM rsshub_feeds WHERE id=?", (feed_id,))
        return feed_from_row(row) if row else None

    def save(self, feed: Feed) -> Feed:
        now = utcnow()
        if feed.id is None:
            feed.id = self._db.execute(
                "INSERT INTO rsshub_feeds(link, title, etag, last_modified, entry_hashes, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (feed.link, feed.title, feed.etag, feed.last_modified, _dump(feed.entry_hashes), now, now),
            )
        else:
            self._db.execute(
                "UPDATE rsshub_feeds SET title=?, etag=?, last_modified=?, entry_hashes=?, updated_at=? WHERE id=?",
                (feed.title, feed.etag, feed.last_modified, _dump(feed.entry_hashes), now, feed.id),
            )
        return feed


class SubscriptionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, sub: Subscription) -> Subscription:
        now = utcnow()
        if sub.id is None:
            sub.id = self._db.execute(
                "INSERT INTO rsshub_subs(user_id, feed_id, title, tags, target_session, platform_name, "
                "interval_minutes, next_check_at, notify, push_mode, handlers_mode, handlers, "
                "length_limit, display_author, display_via, display_title, display_entry_tags, style, display_media, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sub.user_id,
                    sub.feed_id,
                    sub.title,
                    sub.tags,
                    sub.target_session,
                    sub.platform_name,
                    sub.interval_minutes,
                    sub.next_check_at,
                    sub.notify,
                    sub.push_mode,
                    sub.handlers_mode,
                    _dump(sub.handlers),
                    sub.length_limit,
                    sub.display_author,
                    sub.display_via,
                    sub.display_title,
                    sub.display_entry_tags,
                    sub.style,
                    sub.display_media,
                    now,
                    now,
                ),
            )
        else:
            self._db.execute(
                "UPDATE rsshub_subs SET title=?, tags=?, target_session=?, platform_name=?, interval_minutes=?, "
                "next_check_at=?, notify=?, push_mode=?, handlers_mode=?, handlers=?, length_limit=?, "
                "display_author=?, display_via=?, display_title=?, display_entry_tags=?, style=?, display_media=?, "
                "updated_at=? WHERE id=?",
                (
                    sub.title,
                    sub.tags,
                    sub.target_session,
                    sub.platform_name,
                    sub.interval_minutes,
                    sub.next_check_at,
                    sub.notify,
                    sub.push_mode,
                    sub.handlers_mode,
                    _dump(sub.handlers),
                    sub.length_limit,
                    sub.display_author,
                    sub.display_via,
                    sub.display_title,
                    sub.display_entry_tags,
                    sub.style,
                    sub.display_media,
                    now,
                    sub.id,
                ),
            )
        return sub

    def get(self, sub_id: int) -> Subscription | None:
        row = self._db.query_one("SELECT * FROM rsshub_subs WHERE id=?", (sub_id,))
        return sub_from_row(row) if row else None

    def get_by_user_feed_session(self, user_id: str, feed_id: int, target_session: str | None) -> Subscription | None:
        row = self._db.query_one(
            "SELECT * FROM rsshub_subs WHERE user_id=? AND feed_id=? AND target_session IS ?",
            (user_id, feed_id, target_session),
        )
        return sub_from_row(row) if row else None

    def list_by_session(self, target_session: str) -> list[Subscription]:
        rows = self._db.query("SELECT * FROM rsshub_subs WHERE target_session=? ORDER BY id", (target_session,))
        return [sub_from_row(row) for row in rows]

    def list_by_user(self, user_id: str) -> list[Subscription]:
        rows = self._db.query("SELECT * FROM rsshub_subs WHERE user_id=? ORDER BY id", (user_id,))
        return [sub_from_row(row) for row in rows]

    def list_active(self) -> list[Subscription]:
        rows = self._db.query("SELECT * FROM rsshub_subs WHERE state=1 ORDER BY id")
        return [sub_from_row(row) for row in rows]

    def update(self, sub_id: int, **fields) -> None:
        if not fields:
            return
        columns, params = _update_clause(fields, _SUB_COLUMNS)
        self._db.execute(
            f"UPDATE rsshub_subs SET {columns}, updated_at=? WHERE id=?",  # noqa: S608 - 列名经白名单校验
            (*params, utcnow(), sub_id),
        )

    def delete(self, sub_id: int) -> None:
        self._db.execute("DELETE FROM rsshub_subs WHERE id=?", (sub_id,))

    def delete_by_session(self, target_session: str) -> int:
        cursor = self._db.conn.execute("DELETE FROM rsshub_subs WHERE target_session=?", (target_session,))
        self._db.conn.commit()
        return cursor.rowcount or 0


class SessionDefaultsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, session_key: str) -> dict:
        row = self._db.query_one("SELECT defaults FROM rsshub_session_defaults WHERE session_key=?", (session_key,))
        if row is None:
            return {}
        try:
            value = json.loads(row["defaults"])
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def set(self, session_key: str, defaults: dict) -> None:
        self._db.execute(
            "INSERT INTO rsshub_session_defaults(session_key, defaults, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(session_key) DO UPDATE SET defaults=excluded.defaults, updated_at=excluded.updated_at",
            (session_key, json.dumps(defaults, ensure_ascii=False, separators=(",", ":")), utcnow()),
        )


class PushHistoryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, history: PushHistory) -> PushHistory:
        now = utcnow()
        history.id = self._db.execute(
            "INSERT INTO rsshub_push_history(sub_id, user_id, feed_id, source_type, source_key, content, raw_xml, "
            "media_urls, handler_trace, entry_title, entry_link, entry_guid, feed_title, feed_link, platform_name, "
            "target_session, status, retry_count, max_retries, fail_reason, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                history.sub_id,
                history.user_id,
                history.feed_id,
                history.source_type,
                history.source_key,
                history.content,
                history.raw_xml,
                _dump(history.media_urls),
                _dump(history.handler_trace),
                history.entry_title,
                history.entry_link,
                history.entry_guid,
                history.feed_title,
                history.feed_link,
                history.platform_name,
                history.target_session,
                history.status,
                history.retry_count,
                history.max_retries,
                history.fail_reason,
                now,
                now,
            ),
        )
        return history

    def get(self, history_id: int) -> PushHistory | None:
        row = self._db.query_one("SELECT * FROM rsshub_push_history WHERE id=?", (history_id,))
        return history_from_row(row) if row else None

    def update(self, history_id: int, **fields) -> None:
        if not fields:
            return
        columns, params = _update_clause(fields, _HISTORY_COLUMNS)
        self._db.execute(
            f"UPDATE rsshub_push_history SET {columns}, updated_at=? WHERE id=?",  # noqa: S608 - 列名经白名单校验
            (*params, utcnow(), history_id),
        )

    def list_by_session(self, target_session: str, limit: int = 20) -> list[PushHistory]:
        rows = self._db.query(
            "SELECT * FROM rsshub_push_history WHERE target_session=? ORDER BY id DESC LIMIT ?",
            (target_session, limit),
        )
        return [history_from_row(row) for row in rows]

    def list_recent(self, limit: int = 200) -> list[PushHistory]:
        rows = self._db.query(
            "SELECT * FROM rsshub_push_history ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [history_from_row(row) for row in rows]

    def list_retryable(self, limit: int = 50) -> list[PushHistory]:
        rows = self._db.query(
            "SELECT * FROM rsshub_push_history WHERE status='failed' AND retry_count < max_retries ORDER BY id LIMIT ?",
            (limit,),
        )
        return [history_from_row(row) for row in rows]

    def count_for_entry(self, entry_link: str, target_session: str | None) -> int:
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM rsshub_push_history WHERE entry_link=? AND target_session IS ?",
            (entry_link, target_session),
        )
        return int(row["n"]) if row else 0

    def purge_older_than(self, days: int) -> int:
        cursor = self._db.conn.execute(
            "DELETE FROM rsshub_push_history WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        self._db.conn.commit()
        return cursor.rowcount or 0


class Repositories:
    """聚合容器，便于依赖注入。"""

    def __init__(self, db: Database) -> None:
        self.users = UserRepository(db)
        self.feeds = FeedRepository(db)
        self.subs = SubscriptionRepository(db)
        self.session_defaults = SessionDefaultsRepository(db)
        self.history = PushHistoryRepository(db)
