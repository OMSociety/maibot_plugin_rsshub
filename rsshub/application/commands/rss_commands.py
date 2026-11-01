"""聊天命令面：6 个 @Command，其中 /测试推送 只对宿主主人开放。

宿主不会替插件发送命令返回值，所以每个命令都在处理函数里自行 send.text。
命令只覆盖高频动作，其余能力在 Tools 与（三期）WebUI 上。
"""

from __future__ import annotations

from typing import Any

from maibot_sdk import Command

from ...domain.settings import resolve_settings
from ...errors import RsshubError
from ...infrastructure.fetcher.parser import parse_feed
from ..services.content_handler import HandlerContext

_HELP = """RSSHub 订阅命令
/sub <地址或路由> [标题]  订阅一个源
/unsub [编号]             取消订阅，不传编号则清空当前会话
/sub_list                 查看当前会话订阅
/sub_status               查看轮询与推送概况
/测试推送 [编号]          立即把某条订阅的最新内容推到当前会话（仅宿主主人）
/rsshelp                  显示本帮助
更细的配置（推送模式、处理链、历史）可以直接用自然语言让麦麦调用工具。"""


class RssCommandsMixin:
    """命令实现；依赖宿主插件实例上的 ctx / store / 服务对象。"""

    async def _reply(self, stream_id: str, text: str) -> None:
        await self.ctx.send.text(text, stream_id)

    @Command(
        "rss_sub",
        description="订阅 RSS/Atom 源",
        pattern=r"^/sub(?:@\S+)?\s+(?P<link>\S+)(?:\s+(?P<title>.+))?$",
    )
    async def handle_sub(self, stream_id: str = "", matched_groups: dict | None = None, **kwargs: Any) -> tuple:
        groups = matched_groups or {}
        link = str(groups.get("link") or "").strip()
        title = str(groups.get("title") or "").strip()
        if not link:
            await self._reply(stream_id, "用法：/sub <Feed 地址或 RSSHub 路由> [自定义标题]")
            return True, "缺少订阅地址", True
        try:
            sub, created = await self.sub_service.subscribe(
                user_id=str(kwargs.get("user_id") or ""),
                raw_link=link,
                stream_id=stream_id,
                platform=str(kwargs.get("platform") or ""),
                title=title,
            )
        except RsshubError as exc:
            await self._reply(stream_id, f"订阅失败：{exc}")
            return True, "订阅失败", True
        if not created:
            await self._reply(stream_id, f"已经订阅过这个源（订阅 #{sub.id}）")
            return True, "已存在", True
        await self._reply(stream_id, f"已订阅「{sub.title or link}」（订阅 #{sub.id}）")
        return True, "订阅成功", True

    @Command("rss_unsub", description="取消订阅", pattern=r"^/unsub(?:@\S+)?(?:\s+(?P<sub_id>\d+))?$")
    async def handle_unsub(self, stream_id: str = "", matched_groups: dict | None = None, **kwargs: Any) -> tuple:
        raw_id = str((matched_groups or {}).get("sub_id") or "").strip()
        try:
            removed = self.sub_service.unsubscribe(
                str(kwargs.get("user_id") or ""), stream_id, int(raw_id) if raw_id else None
            )
        except RsshubError as exc:
            await self._reply(stream_id, str(exc))
            return True, "订阅不存在", True
        if not removed:
            await self._reply(stream_id, "当前会话没有可取消的订阅")
            return True, "无订阅", True
        await self._reply(stream_id, f"已取消 {removed} 个订阅")
        return True, "取消成功", True

    @Command("rss_sub_list", description="查看当前会话订阅", pattern=r"^/sub_list(?:@\S+)?$")
    async def handle_list(self, stream_id: str = "", **kwargs: Any) -> tuple:
        subs = self.sub_service.list_for_session(stream_id)
        if not subs:
            await self._reply(stream_id, "当前会话还没有订阅，用 /sub <地址> 来添加")
            return True, "无订阅", True
        lines = ["当前会话订阅："]
        for sub in subs:
            info = self.sub_service.describe(sub, self.defaults)
            handlers = "、".join(info["handlers"]) or "无"
            lines.append(
                f"#{info['id']} {info['title'] or info['link']}（{info['push_mode']}，"
                f"{info['interval_seconds'] // 60} 分钟，处理链 {handlers}）"
            )
        await self._reply(stream_id, "\n".join(lines))
        return True, "已列出", True

    @Command("rss_sub_status", description="查看轮询与推送概况", pattern=r"^/sub_status(?:@\S+)?$")
    async def handle_status(self, stream_id: str = "", **kwargs: Any) -> tuple:
        subs = self.sub_service.list_for_session(stream_id)
        total = len(self.store.repos.subs.list_active())
        recent = self.store.repos.history.list_by_session(stream_id, 5)
        failed = sum(1 for record in recent if record.status == "failed")
        lines = [
            f"本会话订阅 {len(subs)} 个，全库启用中 {total} 个",
            f"最近 {len(recent)} 条推送中失败 {failed} 条",
            f"轮询间隔 {int(self.config.polling.interval_minutes)} 分钟，默认推送模式 {self.config.push.default_mode}",
        ]
        await self._reply(stream_id, "\n".join(lines))
        return True, "已输出概况", True

    @Command(
        "rss_test_push",
        description="立即把某条订阅的最新内容推送到当前会话，用于验证源、处理链与推送效果",
        pattern=r"^/(?:测试推送|rss_test)(?:@\S+)?(?:\s+(?P<sub_id>\d+))?$",
        permission="operator",
    )
    async def handle_test_push(
        self,
        stream_id: str = "",
        matched_groups: dict | None = None,
        **kwargs: Any,
    ) -> tuple:
        raw_id = str((matched_groups or {}).get("sub_id") or "").strip()
        subs = self.store.repos.subs.list_by_session(stream_id)
        if not subs:
            await self._reply(stream_id, "当前会话没有订阅，先 /sub <地址>")
            return True, "无订阅", True
        if raw_id:
            sub = next((item for item in subs if item.id == int(raw_id)), None)
            if sub is None:
                await self._reply(stream_id, f"当前会话没有 #{raw_id} 这条订阅，用 /sub_list 看编号")
                return True, "订阅不存在", True
        elif len(subs) == 1:
            sub = subs[0]
        else:
            listing = "\n".join(f"#{item.id} {item.title or item.link}" for item in subs)
            await self._reply(stream_id, f"当前会话有多条订阅，请带编号：/测试推送 <编号>\n{listing}")
            return True, "需要编号", True
        feed = self.store.repos.feeds.get(sub.feed_id)
        if feed is None or not feed.link:
            await self._reply(stream_id, "这条订阅没有可抓取的地址，先取消后重新订阅")
            return True, "源缺失", True
        try:
            result = await self._client.fetch(feed.link)
            document = parse_feed(result.content, result.url or feed.link)
        except Exception as exc:
            await self._reply(stream_id, f"抓取失败：{exc}")
            return True, "抓取失败", True
        entries = document.latest_entries(3)
        if not entries:
            await self._reply(stream_id, "这个源没有抓到任何条目（可能是空源或被站点拦截）")
            return True, "无条目", True

        user_id = str(sub.user_id or "")
        user = self.store.repos.users.get(user_id) if user_id else None
        settings = resolve_settings(sub, user, self.defaults)
        kept = []
        for entry in entries:
            context = HandlerContext.from_entry(entry, feed_title=feed.title or sub.title, feed_link=feed.link)
            outcome = await self.handler_chain.run(settings.handlers, context, session_id=stream_id)
            if outcome.allow:
                kept.append(entry)
        if not kept:
            await self._reply(stream_id, "抓到了条目，但都被处理链拦下了（检查 ai_filter）")
            return True, "全部被过滤", True

        delivered = await self._dispatcher.push(
            sub=sub,
            feed=feed,
            entries=list(reversed(kept)),
            settings=settings,
            stream_id=stream_id,
            record=False,
        )
        if not delivered:
            await self._reply(stream_id, "推送失败，看宿主日志里的 rsshub 记录")
            return True, "推送失败", True
        skipped = len(entries) - len(kept)
        note = f"（{len(kept)} 条，处理链拦下 {skipped} 条）" if skipped else ""
        await self._reply(
            stream_id,
            f"测试推送完成：{sub.title or feed.link}{note}。本次不写推送历史，也不影响定时轮询",
        )
        return True, "测试推送完成", True

    @Command("rss_help", description="RSSHub 订阅帮助", pattern=r"^/rsshelp(?:@\S+)?$")
    async def handle_help(self, stream_id: str = "", **kwargs: Any) -> tuple:
        await self._reply(stream_id, _HELP)
        return True, "已输出帮助", True
