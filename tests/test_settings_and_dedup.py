"""配置继承与去重哈希的单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from rsshub.domain.entry import Entry
from rsshub.domain.settings import GlobalDefaults, resolve_handlers, resolve_settings
from rsshub.infrastructure.fetcher.dedup import entry_hash_set, is_known_entry, merge_hashes
from rsshub.infrastructure.persistence.models import Subscription, User


def _sub(**kwargs: object) -> Subscription:
    base = {"user_id": "u1", "feed_id": 1}
    base.update(kwargs)
    return Subscription(**base)


def test_subscription_overrides_user_then_global() -> None:
    defaults = GlobalDefaults(interval_seconds=600, default_push_mode="persona", notify=1)
    user = User(user_id="u1", interval_minutes=30, notify=0, push_mode="inherit")
    sub = _sub(interval_minutes=5, push_mode="direct")

    settings = resolve_settings(sub, user, defaults)

    assert settings.interval_seconds == 300
    assert settings.push_mode == "direct"
    assert settings.notify is False


def test_inherit_falls_back_to_global_defaults() -> None:
    defaults = GlobalDefaults(interval_seconds=900, default_push_mode="silent")
    settings = resolve_settings(_sub(), User(user_id="u1"), defaults)

    assert settings.interval_seconds == 900
    assert settings.push_mode == "silent"
    assert settings.notify is True


def test_handlers_mode_three_states() -> None:
    user = User(user_id="u1", handlers=[{"id": "f", "type": "builtin", "name": "ai_filter", "config": {}}])
    override = _sub(handlers_mode="override", handlers=[{"id": "t", "type": "builtin", "name": "ai_transform"}])
    disabled = _sub(handlers_mode="disabled", handlers=[{"id": "t", "type": "builtin"}])

    assert resolve_handlers(_sub(handlers_mode="inherit"), user)[0]["id"] == "f"
    assert [spec["id"] for spec in resolve_handlers(override, user)] == ["t"]
    assert resolve_handlers(disabled, user) == []


def test_entry_hash_is_stable_and_content_sensitive() -> None:
    entry = Entry(title="标题", link="https://example.com/a", summary="摘要")
    changed_summary = Entry(title="标题", link="https://example.com/a", summary="换了摘要")
    changed_title = Entry(title="另一个标题", link="https://example.com/a", summary="摘要")

    hashes = entry_hash_set(entry)
    assert hashes == entry_hash_set(entry)
    # sid 哈希按链接识别条目，同一链接下摘要变化只应改变 content 哈希
    assert hashes[0] == entry_hash_set(changed_summary)[0]
    assert hashes[1] != entry_hash_set(changed_summary)[1]
    assert hashes[1] != entry_hash_set(changed_title)[1]


def test_known_entry_and_hash_merge_limit() -> None:
    entry = Entry(guid="g1", title="t", link="https://example.com/1")
    known = set(entry_hash_set(entry))
    assert is_known_entry(entry, known)
    assert not is_known_entry(Entry(guid="g2", link="https://example.com/2"), known)

    merged = merge_hashes([f"old-{i}" for i in range(400)], ["new-1"])
    assert merged[-1] == "new-1"
    assert len(merged) <= 800


def test_dedup_key_prefers_guid_then_link() -> None:
    assert Entry(guid="g", link="l").dedup_key() == "g"
    assert Entry(link="l", entry_id="e").dedup_key() == "l"
    fallback = Entry(title="t", published=datetime(2026, 1, 1, tzinfo=UTC))
    assert fallback.dedup_key().startswith("t|2026-01-01")
