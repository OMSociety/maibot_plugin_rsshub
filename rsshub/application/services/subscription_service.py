"""订阅管理：建/删/改/查，以及会话默认值。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain.settings import GlobalDefaults, resolve_settings
from ...errors import SubscriptionNotFoundError
from ...infrastructure.fetcher.client import HttpClient
from ...infrastructure.fetcher.parser import parse_feed
from ...infrastructure.fetcher.url_guard import normalize_feed_url_async
from ...infrastructure.persistence.database import INHERIT_VALUE
from ...infrastructure.persistence.models import Feed, Subscription, User
from ...infrastructure.persistence.store import Store

SUB_OPTION_KEYS = frozenset(
    {
        "interval",
        "notify",
        "push_mode",
        "length_limit",
        "display_author",
        "display_title",
        "display_entry_tags",
        "display_media",
        "title",
        "tags",
    }
)

INTERVAL_MINUTES_MIN = 1
INTERVAL_MINUTES_MAX = 1440


@dataclass
class SubscribeResult:
    """一次订阅请求的结果：区分「新建订阅」与「该源首次入库」。"""

    sub: Subscription
    created_subscription: bool
    created_feed: bool


def _session_label(platform: str, user_id: str, group_id: str, stream_id: str) -> str:
    """给订阅表生成可读的会话标识：平台名 + 群/私聊 + ID。

    例如 ``QQ群:123456789`` / ``QQ私聊:123456789``；宿主传的 stream_id 是内部哈希，
    人看不出是哪个会话，这里按调用现场的平台、群号、用户号拼一个稳定标签，拿不到
    平台信息就退回原 stream_id。
    """
    name = _PLATFORM_NAMES.get(platform.strip().lower(), platform.strip())
    if not platform.strip():
        return stream_id
    if group_id.strip():
        return f"{name}群:{group_id.strip()}"
    if user_id.strip():
        return f"{name}私聊:{user_id.strip()}"
    return f"{name}:{stream_id}"


_PLATFORM_NAMES = {
    "qq": "QQ",
    "qq_official": "QQ官方",
    "wechat": "微信",
    "weixin": "微信",
    "telegram": "Telegram",
    "discord": "Discord",
}


class SubscriptionService:
    """订阅与用户设置的业务操作。"""

    def __init__(self, ctx: Any, store: Store, config: Any, client: HttpClient) -> None:
        self._ctx = ctx
        self._store = store
        self._config = config
        self._client = client

    # ── 订阅 ────────────────────────────────────────────────────

    async def subscribe(
        self,
        *,
        user_id: str,
        raw_link: str,
        stream_id: str,
        platform: str = "",
        group_id: str = "",
        title: str = "",
        tags: str = "",
    ) -> SubscribeResult:
        link = await normalize_feed_url_async(
            raw_link,
            allow_private=bool(self._config.network.allow_private_addresses),
            rsshub_base_url=str(self._config.rsshub.base_url),
        )

        feed = self._store.repos.feeds.get_by_link(link)
        created_feed = False
        if feed is None:
            feed = Feed(link=link)
            feed.title = await self._probe_title(link)
            feed = self._store.repos.feeds.save(feed)
            created_feed = True

        existing = self._store.repos.subs.get_by_user_feed_session(user_id, feed.id or 0, stream_id)
        if existing is not None:
            return SubscribeResult(sub=existing, created_subscription=False, created_feed=created_feed)

        self._store.repos.users.get_or_create(user_id)
        sub = Subscription(
            user_id=user_id,
            feed_id=feed.id or 0,
            title=title or feed.title,
            tags=tags,
            target_session=stream_id,
            platform_name=platform,
            session_label=_session_label(platform, user_id, group_id, stream_id),
        )
        sub = self._store.repos.subs.save(sub)
        return SubscribeResult(sub=sub, created_subscription=True, created_feed=created_feed)

    def unsubscribe(self, user_id: str, stream_id: str, sub_id: int | None = None) -> int:
        if sub_id is not None:
            sub = self._store.repos.subs.get(sub_id)
            if sub is None or sub.user_id != user_id or sub.target_session != stream_id:
                raise SubscriptionNotFoundError(f"订阅不存在: {sub_id}")
            self._store.repos.subs.delete(sub_id)
            return 1
        return self._store.repos.subs.delete_by_session(stream_id)

    def list_for_session(self, stream_id: str) -> list[Subscription]:
        return self._store.repos.subs.list_by_session(stream_id)

    def list_for_user(self, user_id: str) -> list[Subscription]:
        return self._store.repos.subs.list_by_user(user_id)

    # ── 选项写入 ────────────────────────────────────────────────

    def set_subscription_option(self, user_id: str, sub_id: int | None, key: str, value: Any) -> Subscription:
        if key not in SUB_OPTION_KEYS:
            raise ValueError(f"不支持的选项: {key}")
        sub = self._resolve_sub(user_id, sub_id)
        self._store.repos.subs.update(sub.id, **{_column(key): _coerce(key, value)})
        refreshed = self._store.repos.subs.get(sub.id)
        assert refreshed is not None
        return refreshed

    def set_user_option(self, user_id: str, key: str, value: Any) -> User:
        if key not in SUB_OPTION_KEYS:
            raise ValueError(f"不支持的选项: {key}")
        self._store.repos.users.get_or_create(user_id)
        self._store.repos.users.update(user_id, **{_column(key): _coerce(key, value)})
        user = self._store.repos.users.get(user_id)
        assert user is not None
        return user

    def get_session_defaults(self, stream_id: str) -> dict[str, Any]:
        return self._store.repos.session_defaults.get(stream_id)

    def set_session_defaults(self, stream_id: str, options: dict[str, Any]) -> dict[str, Any]:
        current = self.get_session_defaults(stream_id)
        for key, value in options.items():
            if key in SUB_OPTION_KEYS:
                current[key] = value
        self._store.repos.session_defaults.set(stream_id, current)
        return current

    # ── 生效配置展示 ────────────────────────────────────────────

    def describe(self, sub: Subscription, defaults: GlobalDefaults) -> dict[str, Any]:
        user = self._store.repos.users.get(sub.user_id)
        session = self._store.repos.session_defaults.get(sub.target_session or "")
        settings = resolve_settings(sub, user, defaults, session)
        feed = self._store.repos.feeds.get(sub.feed_id)
        return {
            "id": sub.id,
            "title": sub.title or (feed.title if feed else ""),
            "link": feed.link if feed else "",
            "state": sub.state,
            "push_mode": settings.push_mode,
            "notify": settings.notify,
            "display_media": settings.display_media,
            "interval_seconds": settings.interval_seconds,
            "handlers": [spec.get("type") for spec in settings.handlers],
            "next_check_at": sub.next_check_at,
        }

    # ── 内部 ────────────────────────────────────────────────────

    def _resolve_sub(self, user_id: str, sub_id: int | None) -> Subscription:
        if sub_id is None:
            subs = self._store.repos.subs.list_by_user(user_id)
            if not subs:
                raise SubscriptionNotFoundError("你还没有订阅")
            return subs[0]
        sub = self._store.repos.subs.get(sub_id)
        if sub is None or sub.user_id != user_id:
            raise SubscriptionNotFoundError(f"订阅不存在: {sub_id}")
        return sub

    async def _probe_title(self, link: str) -> str:
        try:
            result = await self._client.fetch(link)
        except Exception as exc:
            self._ctx.logger.info("订阅源标题探测失败 %s: %s", link, exc)
            return ""
        try:
            return parse_feed(result.content, result.url).title
        except Exception as exc:
            self._ctx.logger.info("订阅源内容解析失败 %s: %s", link, exc)
            return ""


def _column(key: str) -> str:
    return {
        "interval": "interval_minutes",
        "push_mode": "push_mode",
        "notify": "notify",
        "length_limit": "length_limit",
        "display_author": "display_author",
        "display_title": "display_title",
        "display_entry_tags": "display_entry_tags",
        "display_media": "display_media",
        "title": "title",
        "tags": "tags",
    }[key]


def _coerce(key: str, value: Any) -> Any:
    if key in {"push_mode", "title", "tags"}:
        return str(value)
    if key == "notify":
        return 1 if bool(value) else 0
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"选项 {key} 需要整数，收到: {value!r}") from exc
    if key == "interval":
        if number == 0:
            return INHERIT_VALUE
        if not INTERVAL_MINUTES_MIN <= number <= INTERVAL_MINUTES_MAX:
            raise ValueError(
                f"轮询间隔需在 {INTERVAL_MINUTES_MIN}-{INTERVAL_MINUTES_MAX} 分钟之间（0 表示继承默认），收到: {number}"
            )
    return number
