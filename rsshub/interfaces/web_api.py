"""WebUI 管理接口：订阅列表、推送历史、参数修改与立即抓取。

页面声明在 webui.json，前端在 webui/index.html；这里只提供静态 API，
参数与返回都满足宿主 WebUI 协议（返回 ≤ 512 KiB、单次预算 10 秒）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from maibot_sdk import API

from ..application.services.subscription_service import SUB_OPTION_KEYS
from ..constants import HANDLERS
from ..errors import RsshubError

_MAX_PAGE_SIZE = 100
_VALID_STATUS = {"success", "failed", "stopped", "skipped", "pending", "retrying"}


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="seconds")
    return str(value or "")


class WebApiMixin:
    """WebUI 查询与操作；依赖插件实例上的 store / sub_service / config。"""

    # ── 查询 ────────────────────────────────────────────────────

    @API("webui_overview", description="订阅与推送概况")
    async def webui_overview(self) -> dict[str, Any]:
        if self.store is None:
            return {"ready": False, "error": "插件尚未加载"}
        subs = self.store.repos.subs.list_active()
        since = datetime.now(UTC) - timedelta(days=1)
        recent = self.store.repos.history.list_recent(500)
        today = [record for record in recent if _parse(record.created_at) >= since]
        failures = [record for record in today if record.status in {"failed", "stopped"}]
        feeds = {sub.feed_id for sub in subs}
        return {
            "ready": True,
            "subscriptions": len(subs),
            "feeds": len(feeds),
            "sessions": len({sub.target_session for sub in subs if sub.target_session}),
            "pushes_24h": len(today),
            "failures_24h": len(failures),
            "pending_retry": len(self.store.repos.history.list_retryable(500)),
            "poll_interval_minutes": int(self.config.polling.interval_seconds) // 60,
            "default_push_mode": str(self.config.push.default_mode),
            "handlers": list(HANDLERS),
        }

    @API("webui_list_subscriptions", description="订阅列表分页")
    async def webui_list_subscriptions(self, page: int = 1, page_size: int = 20, keyword: str = "") -> dict[str, Any]:
        if self.store is None:
            return {"items": [], "total": 0, "page": 1, "pages": 0}
        rows = self.store.repos.subs.list_active()
        keyword = keyword.strip().lower()
        if keyword:
            rows = [
                sub
                for sub in rows
                if keyword in (sub.title or "").lower()
                or keyword in (sub.target_session or "").lower()
                or keyword
                in str(self.store.repos.feeds.get(sub.feed_id).link if self.store.repos.feeds.get(sub.feed_id) else "")
            ]
        page, page_size = _paginate(page, page_size, len(rows))
        start = (page - 1) * page_size
        items = []
        for sub in rows[start : start + page_size]:
            feed = self.store.repos.feeds.get(sub.feed_id)
            items.append(
                {
                    "id": sub.id,
                    "title": sub.title or (feed.title if feed else ""),
                    "link": feed.link if feed else "",
                    "session": sub.target_session or "",
                    "platform": sub.platform_name or "",
                    "push_mode": sub.push_mode,
                    "handlers_mode": sub.handlers_mode,
                    "notify": sub.notify,
                    "interval_minutes": sub.interval_minutes,
                    "next_check_at": _iso(sub.next_check_at),
                    "state": sub.state,
                }
            )
        total = len(rows)
        return {"items": items, "total": total, "page": page, "pages": max(1, (total + page_size - 1) // page_size)}

    @API("webui_list_history", description="推送历史分页")
    async def webui_list_history(self, page: int = 1, page_size: int = 20, status: str = "") -> dict[str, Any]:
        if self.store is None:
            return {"items": [], "total": 0, "page": 1, "pages": 0}
        rows = self.store.repos.history.list_recent(1000)
        status = status.strip().lower()
        if status in _VALID_STATUS:
            rows = [record for record in rows if record.status == status]
        page, page_size = _paginate(page, page_size, len(rows))
        start = (page - 1) * page_size
        items = [
            {
                "id": record.id,
                "title": record.entry_title,
                "link": record.entry_link,
                "feed_title": record.feed_title,
                "session": record.target_session or "",
                "status": record.status or "",
                "retry_count": record.retry_count,
                "fail_reason": record.fail_reason or "",
                "created_at": _iso(record.created_at),
            }
            for record in rows[start : start + page_size]
        ]
        total = len(rows)
        return {"items": items, "total": total, "page": page, "pages": max(1, (total + page_size - 1) // page_size)}

    @API("webui_get_subscription", description="单个订阅详情")
    async def webui_get_subscription(self, sub_id: int = 0) -> dict[str, Any]:
        sub = self._web_sub(sub_id)
        feed = self.store.repos.feeds.get(sub.feed_id) if self.store else None
        settings = self.sub_service.describe(sub, self.defaults) if self.sub_service else {}
        return {
            "id": sub.id,
            "title": sub.title,
            "link": feed.link if feed else "",
            "session": sub.target_session or "",
            "settings": settings,
            "handlers": sub.handlers,
            "handlers_mode": sub.handlers_mode,
            "option_keys": sorted(SUB_OPTION_KEYS),
        }

    # ── 操作 ────────────────────────────────────────────────────

    @API("webui_update_subscription", description="修改订阅选项")
    async def webui_update_subscription(self, sub_id: int = 0, key: str = "", value: str = "") -> dict[str, Any]:
        sub = self._web_sub(sub_id)
        if key not in SUB_OPTION_KEYS:
            raise ValueError(f"不支持的选项: {key}")
        if self.sub_service is None:
            raise RuntimeError("插件尚未加载")
        updated = self.sub_service.set_subscription_option(sub.user_id, sub.id, key, value)
        return {"id": updated.id, "key": key, "value": getattr(updated, _column_name(key), None)}

    @API("webui_set_subscription_state", description="启用或停用订阅")
    async def webui_set_subscription_state(self, sub_id: int = 0, enabled: bool = True) -> dict[str, Any]:
        sub = self._web_sub(sub_id)
        if self.store is None:
            raise RuntimeError("插件尚未加载")
        self.store.repos.subs.update(sub.id, state=1 if enabled else 0)
        return {"id": sub.id, "enabled": bool(enabled)}

    @API("webui_delete_subscription", description="删除订阅")
    async def webui_delete_subscription(self, sub_id: int = 0) -> dict[str, Any]:
        sub = self._web_sub(sub_id)
        if self.store is None:
            raise RuntimeError("插件尚未加载")
        self.store.repos.subs.delete(sub.id)
        return {"id": sub.id, "deleted": True}

    @API("webui_poll_now", description="立即抓取该订阅源")
    async def webui_poll_now(self, sub_id: int = 0) -> dict[str, Any]:
        sub = self._web_sub(sub_id)
        if self.store is None or self._poller is None:
            raise RuntimeError("插件尚未加载")
        self.store.repos.subs.update(sub.id, next_check_at=datetime.now(UTC) - timedelta(seconds=1))
        return {"id": sub.id, "queued": True}

    @API("webui_import_subscriptions", description="批量导入订阅")
    async def webui_import_subscriptions(self, links: str = "", session: str = "", title: str = "") -> dict[str, Any]:
        if self.sub_service is None:
            raise RuntimeError("插件尚未加载")
        imported, failed = 0, []
        for raw in [line.strip() for line in links.replace(",", "\n").splitlines()]:
            if not raw:
                continue
            try:
                await self.sub_service.subscribe(user_id="webui", raw_link=raw, stream_id=session, title=title)
                imported += 1
            except (RsshubError, ValueError) as exc:
                failed.append(f"{raw}: {exc}")
        return {"imported": imported, "failed": failed}

    # ── 内部 ────────────────────────────────────────────────────

    def _web_sub(self, sub_id: int) -> Any:
        if self.store is None:
            raise RuntimeError("插件尚未加载")
        sub = self.store.repos.subs.get(int(sub_id or 0))
        if sub is None:
            raise ValueError(f"订阅不存在: {sub_id}")
        return sub


def _paginate(page: int, page_size: int, total: int) -> tuple[int, int]:
    page_size = max(1, min(int(page_size or 20), _MAX_PAGE_SIZE))
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(int(page or 1), pages))
    return page, page_size


def _parse(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)


def _column_name(key: str) -> str:
    return "interval_minutes" if key == "interval" else key
