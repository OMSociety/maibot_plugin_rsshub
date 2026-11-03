"""四级配置继承解析：订阅 → 会话默认 → 用户 → 全局默认。

继承规则与源插件一致：字段值为 INHERIT_VALUE（-100）或 push_mode/handlers_mode
为 'inherit' 时向上继承；handlers_mode 的 override 覆盖、disabled 表示停用全部 handler。
会话默认值来自会话默认表（用户在某会话里设过的偏好），新订阅没写死字段时会用到它。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..infrastructure.persistence.database import INHERIT_MODE, INHERIT_VALUE
from ..infrastructure.persistence.models import Subscription, User


@dataclass
class GlobalDefaults:
    """宿主 config.toml 里的全局默认值。"""

    interval_seconds: int = 600
    default_push_mode: str = "persona"
    notify: int = 1
    length_limit: int = 0
    display_author: int = 0
    display_via: int = 0
    display_title: int = 0
    display_entry_tags: int = 0
    style: int = 0
    display_media: int = 0


@dataclass
class EffectiveSettings:
    interval_seconds: int
    notify: bool
    push_mode: str
    length_limit: int
    display_author: int
    display_via: int
    display_title: int
    display_entry_tags: int
    style: int
    display_media: int
    handlers: list[dict[str, Any]] = field(default_factory=list)


def _pick(*values: Any, default: Any) -> Any:
    """按优先级取第一个非继承值。"""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value == INHERIT_MODE:
                continue
        elif value == INHERIT_VALUE:
            continue
        return value
    return default


def _session_value(session: dict[str, Any] | None, key: str) -> Any:
    """会话默认表里的一项；没设过或存的是继承哨兵都返回 None。"""
    if not session:
        return None
    value = session.get(key)
    if value is None:
        return None
    if isinstance(value, str) and value == INHERIT_MODE:
        return None
    if value == INHERIT_VALUE:
        return None
    return value


def resolve_handlers(sub: Subscription | None, user: User | None) -> list[dict[str, Any]]:
    if sub is not None:
        if sub.handlers_mode == "disabled":
            return []
        if sub.handlers_mode == "override":
            return list(sub.handlers)
    if user is not None:
        return list(user.handlers)
    return []


def resolve_settings(
    sub: Subscription | None,
    user: User | None,
    defaults: GlobalDefaults,
    session: dict[str, Any] | None = None,
) -> EffectiveSettings:
    """逐字段解析生效配置。"""
    interval_minutes = _pick(
        sub.interval_minutes if sub else None,
        _session_value(session, "interval"),
        user.interval_minutes if user else None,
        default=None,
    )
    interval_seconds = int(interval_minutes) * 60 if interval_minutes is not None else defaults.interval_seconds

    push_mode = _pick(
        sub.push_mode if sub else None,
        _session_value(session, "push_mode"),
        user.push_mode if user else None,
        default=defaults.default_push_mode,
    )

    notify_value = _pick(
        sub.notify if sub else None,
        _session_value(session, "notify"),
        user.notify if user else None,
        default=defaults.notify,
    )

    return EffectiveSettings(
        interval_seconds=interval_seconds,
        notify=bool(notify_value),
        push_mode=str(push_mode),
        length_limit=int(
            _pick(
                sub.length_limit if sub else None,
                _session_value(session, "length_limit"),
                user.length_limit if user else None,
                default=defaults.length_limit,
            )
        ),
        display_author=int(
            _pick(
                sub.display_author if sub else None,
                _session_value(session, "display_author"),
                user.display_author if user else None,
                default=defaults.display_author,
            )
        ),
        display_via=int(
            _pick(
                sub.display_via if sub else None,
                user.display_via if user else None,
                default=defaults.display_via,
            )
        ),
        display_title=int(
            _pick(
                sub.display_title if sub else None,
                _session_value(session, "display_title"),
                user.display_title if user else None,
                default=defaults.display_title,
            )
        ),
        display_entry_tags=int(
            _pick(
                sub.display_entry_tags if sub else None,
                _session_value(session, "display_entry_tags"),
                user.display_entry_tags if user else None,
                default=defaults.display_entry_tags,
            )
        ),
        style=int(
            _pick(
                sub.style if sub else None,
                user.style if user else None,
                default=defaults.style,
            )
        ),
        display_media=int(
            _pick(
                sub.display_media if sub else None,
                _session_value(session, "display_media"),
                user.display_media if user else None,
                default=defaults.display_media,
            )
        ),
        handlers=resolve_handlers(sub, user),
    )
