"""LLM 工具面：14 个 @Tool。

工具面向模型，参数说明即模型可见文档；业务逻辑转发给 src 下的服务层。
"""

from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path
from typing import Any

from maibot_sdk import Tool

from ...domain.settings import resolve_settings
from ...errors import RsshubError
from ...infrastructure.media.downloader import (
    FileAttachments,
    av_link_card_text,
    collect_media_candidates,
)
from ...infrastructure.persistence.models import PushHistory
from ..services.content_handler import HandlerContext
from ..services.message_format import build_direct_text


def tools_enabled(func: Any) -> Any:
    """配置开关关闭时让工具直接回绝，避免调用方拿到半可用状态。"""

    @functools.wraps(func)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        plugin_cfg = getattr(getattr(self, "config", None), "plugin", None)
        if plugin_cfg is not None and not bool(getattr(plugin_cfg, "tools_enabled", True)):
            return {"success": False, "content": "订阅工具已在插件配置里关闭，改用 /订阅 等命令或让主人重新打开"}
        return await func(self, *args, **kwargs)

    return wrapper


MAX_XML_BYTES = 256 * 1024
MAX_XML_NODES = 4096

_OPTION_HELP = (
    "可选项：interval（轮询间隔，分钟）、notify（是否推送）、push_mode（persona/direct/silent）、"
    "length_limit（摘要长度上限，0 不限）、display_author/display_via/display_title/display_entry_tags/style/"
    "display_media（0 继承、1 显示、2 隐藏）、title（自定义标题）、tags（标签）"
)

_HANDLER_TEMPLATE: dict[str, Any] = {
    "id": "ai_filter",
    "type": "ai_filter",
    "status": 1,
    "config": {
        "prompt": "",
        "input_scope": "text",
        "reason_max_length": 120,
    },
}


class RssToolsMixin:
    """工具实现；依赖宿主插件实例上的 ctx / store / 服务对象。"""

    # ── 订阅 ────────────────────────────────────────────────────

    @Tool(
        "rss_subscribe",
        description="订阅一个 RSS/Atom/JSON Feed 地址，或 RSSHub 短路由（以 / 开头）",
        parameters={
            "link": {"type": "string", "description": "Feed 地址或 RSSHub 路由，如 /github/issue/xxx"},
            "title": {"type": "string", "description": "自定义订阅标题，留空用订阅源标题", "required": False},
            "tags": {"type": "string", "description": "订阅标签，逗号分隔", "required": False},
        },
    )
    @tools_enabled
    async def rss_subscribe(
        self, link: str = "", title: str = "", tags: str = "", stream_id: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        if not link.strip():
            return {"success": False, "content": "请给出要订阅的 Feed 地址"}
        try:
            sub, created = await self.sub_service.subscribe(
                user_id=str(kwargs.get("user_id") or ""),
                raw_link=link,
                stream_id=stream_id,
                platform=str(kwargs.get("platform") or ""),
                title=title.strip(),
                tags=tags.strip(),
            )
        except RsshubError as exc:
            return {"success": False, "content": f"订阅失败：{exc}"}
        if not created:
            return {"success": True, "content": f"已经订阅过这个源（订阅 #{sub.id}），无需重复添加"}
        return {
            "success": True,
            "content": f"已订阅「{sub.title or link}」（订阅 #{sub.id}），之后有新内容会推送到当前会话",
            "structured_content": {"subscription_id": sub.id},
        }

    @Tool(
        "rss_unsubscribe",
        description="取消一个订阅；不传订阅编号时取消当前会话的全部订阅",
        parameters={
            "subscription_id": {
                "type": "integer",
                "description": "订阅编号，见 rss_list_subscriptions",
                "required": False,
            },
        },
    )
    @tools_enabled
    async def rss_unsubscribe(self, subscription_id: int = 0, stream_id: str = "", **kwargs: Any) -> dict[str, Any]:
        try:
            removed = self.sub_service.unsubscribe(str(kwargs.get("user_id") or ""), stream_id, subscription_id or None)
        except RsshubError as exc:
            return {"success": False, "content": str(exc)}
        if not removed:
            return {"success": False, "content": "当前会话没有可取消的订阅"}
        return {"success": True, "content": f"已取消 {removed} 个订阅"}

    @Tool("rss_unsubscribe_all", description="取消当前会话的全部订阅")
    @tools_enabled
    async def rss_unsubscribe_all(self, stream_id: str = "", **kwargs: Any) -> dict[str, Any]:
        removed = self.store.repos.subs.delete_by_session(stream_id)
        if not removed:
            return {"success": False, "content": "当前会话没有订阅"}
        return {"success": True, "content": f"已取消当前会话全部 {removed} 个订阅"}

    @Tool("rss_list_subscriptions", description="列出当前会话的订阅及其推送配置")
    @tools_enabled
    async def rss_list_subscriptions(self, stream_id: str = "", **kwargs: Any) -> dict[str, Any]:
        subs = self.sub_service.list_for_session(stream_id)
        if not subs:
            return {"success": True, "content": "当前会话还没有订阅"}
        lines = []
        for sub in subs:
            info = self.sub_service.describe(sub, self.defaults)
            handlers = "、".join(info["handlers"]) or "无"
            lines.append(
                f"#{info['id']} {info['title'] or info['link']}\n"
                f"  推送={info['push_mode']} 间隔={info['interval_seconds'] // 60}分钟 处理链={handlers}\n"
                f"  下次抓取={info['next_check_at'] or '待定'}"
            )
        return {"success": True, "content": "\n".join(lines), "structured_content": {"count": len(subs)}}

    @Tool(
        "rss_set_subscription_option",
        description="修改某个订阅的选项",
        parameters={
            "subscription_id": {"type": "integer", "description": "订阅编号"},
            "option": {"type": "string", "description": _OPTION_HELP},
            "value": {"type": "string", "description": "选项值，按 option 的类型传整数/字符串"},
        },
    )
    @tools_enabled
    async def rss_set_subscription_option(
        self, subscription_id: int = 0, option: str = "", value: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        try:
            sub = self.sub_service.set_subscription_option(
                str(kwargs.get("user_id") or ""), subscription_id or None, option, value
            )
        except (RsshubError, ValueError) as exc:
            return {"success": False, "content": f"修改失败：{exc}"}
        return {"success": True, "content": f"订阅 #{sub.id} 的 {option} 已改为 {value}"}

    @Tool(
        "rss_set_user_default_option",
        description="修改当前用户的全局默认选项，影响该用户所有未单独设置的订阅",
        parameters={
            "option": {"type": "string", "description": _OPTION_HELP},
            "value": {"type": "string", "description": "选项值"},
        },
    )
    @tools_enabled
    async def rss_set_user_default_option(self, option: str = "", value: str = "", **kwargs: Any) -> dict[str, Any]:
        try:
            self.sub_service.set_user_option(str(kwargs.get("user_id") or ""), option, value)
        except (RsshubError, ValueError) as exc:
            return {"success": False, "content": f"修改失败：{exc}"}
        return {"success": True, "content": f"你的默认 {option} 已改为 {value}"}

    @Tool(
        "rss_set_session_default_option",
        description="修改当前会话的默认选项，新订阅会继承这些设置",
        parameters={
            "option": {"type": "string", "description": _OPTION_HELP},
            "value": {"type": "string", "description": "选项值"},
        },
    )
    @tools_enabled
    async def rss_set_session_default_option(
        self, option: str = "", value: str = "", stream_id: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        try:
            updated = self.sub_service.set_session_defaults(stream_id, {option: value})
        except (RsshubError, ValueError) as exc:
            return {"success": False, "content": f"修改失败：{exc}"}
        return {"success": True, "content": f"当前会话默认值已更新：{json.dumps(updated, ensure_ascii=False)}"}

    @Tool("rss_get_session_defaults", description="查看当前会话的默认推送设置")
    @tools_enabled
    async def rss_get_session_defaults(self, stream_id: str = "", **kwargs: Any) -> dict[str, Any]:
        defaults = self.sub_service.get_session_defaults(stream_id)
        if not defaults:
            return {"success": True, "content": "当前会话没有单独设置，使用全局默认"}
        return {"success": True, "content": json.dumps(defaults, ensure_ascii=False, indent=2)}

    # ── 推送历史与手工推送 ──────────────────────────────────────

    @Tool(
        "rss_list_push_history",
        description="查看当前会话最近的推送记录与失败原因",
        parameters={"limit": {"type": "integer", "description": "返回条数，默认 10", "required": False}},
    )
    @tools_enabled
    async def rss_list_push_history(self, limit: int = 10, stream_id: str = "", **kwargs: Any) -> dict[str, Any]:
        records = self.store.repos.history.list_by_session(stream_id, max(1, min(limit, 50)))
        if not records:
            return {"success": True, "content": "没有推送记录"}
        lines = [
            f"#{record.id} [{record.status}] {record.entry_title}\n"
            f"  {record.created_at}" + (f"\n  原因：{record.fail_reason}" if record.fail_reason else "")
            for record in records
        ]
        return {"success": True, "content": "\n".join(lines)}

    @Tool(
        "rss_push_xml_entry",
        description="把一段自己写的条目 XML 推送到当前会话（用于播报自定义内容）",
        parameters={
            "source_key": {"type": "string", "description": "来源标识，用于去重与审计"},
            "title": {"type": "string", "description": "条目标题"},
            "xml": {
                "type": "string",
                "description": "条目 XML 片段，支持 title/link/description/content:encoded/category",
            },
            "link": {"type": "string", "description": "条目链接", "required": False},
            "tags": {"type": "string", "description": "标签，逗号分隔", "required": False},
        },
    )
    @tools_enabled
    async def rss_push_xml_entry(
        self,
        source_key: str = "",
        title: str = "",
        xml: str = "",
        link: str = "",
        tags: str = "",
        stream_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not (source_key and title and xml):
            return {"success": False, "content": "source_key、title、xml 都不能为空"}
        if len(xml.encode("utf-8")) > MAX_XML_BYTES:
            return {"success": False, "content": "XML 超过 256KiB 上限"}
        upper = xml.upper()
        if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
            return {"success": False, "content": "XML 不允许包含 DOCTYPE 或 ENTITY 声明"}
        if xml.count("<") > MAX_XML_NODES:
            return {"success": False, "content": "XML 节点数超过 4096 上限"}

        from ...infrastructure.fetcher.parser import parse_feed

        skeleton = (
            '<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>'
            f"<title>{source_key}</title><item>{xml}</item></channel></rss>"
        )
        try:
            document = parse_feed(skeleton.encode("utf-8"), "agent://" + source_key)
        except RsshubError as exc:
            return {"success": False, "content": f"XML 解析失败：{exc}"}
        if not document.entries:
            return {"success": False, "content": "XML 里没有可推送的条目"}
        entry = document.entries[0]
        entry.title = title
        if link:
            entry.link = link
        if tags:
            entry.tags = [item.strip() for item in tags.split(",") if item.strip()]

        guid = f"agent:{hashlib.sha256(f'{source_key}|{title}|{xml}'.encode()).hexdigest()[:16]}"
        if self.store.repos.history.count_for_entry(guid, stream_id) > 0:
            return {"success": True, "content": "这条内容已经推送过，跳过"}

        sub = self.store.repos.subs.list_by_session(stream_id)
        user_id = str(kwargs.get("user_id") or "")
        user = self.store.repos.users.get(user_id) if user_id else None
        settings = resolve_settings(sub[0] if sub else None, user, self.defaults)
        av_links = collect_media_candidates(entry)[1] if settings.display_media >= 0 else []
        attached = await self._attach_av_files(av_links)
        text = build_direct_text(source_key, [entry], settings)
        remaining = av_links[attached:]
        if remaining:
            text = f"{text}\n{av_link_card_text(remaining)}"
        sent = await self.ctx.send.text(text, stream_id)
        delivered = bool(sent.get("sent")) if isinstance(sent, dict) else bool(sent)
        record = PushHistory(
            user_id=str(kwargs.get("user_id") or ""),
            source_type="agent",
            source_key=source_key,
            content=text,
            raw_xml=xml,
            entry_title=title,
            entry_link=link,
            entry_guid=guid,
            feed_title=source_key,
            platform_name=str(kwargs.get("platform") or ""),
            target_session=stream_id,
            status="success" if delivered else "failed",
            fail_reason=None if delivered else "发送失败",
        )
        self.store.repos.history.add(record)
        if not delivered:
            return {"success": False, "content": "推送发送失败，已记入历史等待重试"}
        note = f"，附 {attached} 个音视频文件" if attached else ""
        return {
            "success": True,
            "content": f"已推送到当前会话{note}",
            "structured_content": {"history_id": record.id, "attachments": attached},
        }

    async def _attach_av_files(self, links: list[str]) -> int:
        """把音视频按原文件送达当前工具调用会话，返回成功的文件数。

        宿主只为模型工具调用签发文件凭证，因此该路径只在工具内可用；下载失败或超出
        数量/体积上限的条目由调用方改为在正文里附链接。
        """
        if not links:
            return 0
        media = getattr(self.config, "media", None)
        client = getattr(self, "_client", None)
        if media is None or client is None or not media.attach_files:
            return 0
        attachments = FileAttachments(
            self.ctx,
            client,
            max_files=int(media.max_files),
            max_bytes=int(media.file_max_mib) * 1024 * 1024,
        )
        return await attachments.deliver(links, Path(self.ctx.paths.runtime_dir) / "rsshub-media")

    # ── 内容处理链 ──────────────────────────────────────────────

    @Tool("rss_list_handlers", description="列出内置的内容处理 handler 及其配置写法")
    @tools_enabled
    async def rss_list_handlers(self) -> dict[str, Any]:
        return {
            "success": True,
            "content": (
                "内置 handler：\n"
                "1) ai_filter —— 让模型判断是否值得推送，只有明确 allow=false 才拦截\n"
                "   配置：prompt（自定义提示词）、input_scope（text/raw_xml/both）、reason_max_length\n"
                "2) ai_transform —— 让模型改写标题/摘要/正文（scope=plaintext）\n"
                "   配置：prompt（自定义提示词）\n\n"
                "handler 数组按顺序执行，元素形如：\n" + json.dumps(_HANDLER_TEMPLATE, ensure_ascii=False, indent=2)
            ),
        }

    @Tool(
        "rss_get_handlers",
        description="查看订阅或用户当前启用的内容处理链",
        parameters={
            "subscription_id": {"type": "integer", "description": "订阅编号，0 表示查看用户级", "required": False},
        },
    )
    @tools_enabled
    async def rss_get_handlers(self, subscription_id: int = 0, **kwargs: Any) -> dict[str, Any]:
        user_id = str(kwargs.get("user_id") or "")
        if subscription_id:
            sub = self.store.repos.subs.get(subscription_id)
            if sub is None:
                return {"success": False, "content": f"订阅不存在: {subscription_id}"}
            return {
                "success": True,
                "content": json.dumps(
                    {"mode": sub.handlers_mode, "handlers": sub.handlers}, ensure_ascii=False, indent=2
                ),
            }
        user = self.store.repos.users.get(user_id)
        handlers = user.handlers if user else []
        return {"success": True, "content": json.dumps(handlers, ensure_ascii=False, indent=2)}

    @Tool(
        "rss_set_subscription_handlers",
        description="设置某个订阅的内容处理链",
        parameters={
            "subscription_id": {"type": "integer", "description": "订阅编号"},
            "handlers_json": {
                "type": "string",
                "description": "handler 数组的 JSON 文本；传 [] 清空，传 null 表示继承用户级",
            },
        },
    )
    @tools_enabled
    async def rss_set_subscription_handlers(
        self, subscription_id: int = 0, handlers_json: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        sub = self.store.repos.subs.get(subscription_id)
        if sub is None:
            return {"success": False, "content": f"订阅不存在: {subscription_id}"}
        if handlers_json.strip().lower() in {"null", "inherit"}:
            self.store.repos.subs.update(sub.id, handlers_mode="inherit")
            return {"success": True, "content": "已改为继承用户级处理链"}
        try:
            handlers = json.loads(handlers_json) if handlers_json.strip() else []
        except ValueError as exc:
            return {"success": False, "content": f"JSON 解析失败：{exc}"}
        if not isinstance(handlers, list):
            return {"success": False, "content": "handlers_json 必须是数组"}
        self.store.repos.subs.update(sub.id, handlers_mode="override", handlers=handlers)
        return {"success": True, "content": f"订阅 #{sub.id} 的处理链已更新为 {len(handlers)} 个 handler"}

    @Tool(
        "rss_set_user_handlers",
        description="设置当前用户的默认内容处理链，作用于所有继承用户的订阅",
        parameters={
            "handlers_json": {"type": "string", "description": "handler 数组的 JSON 文本；传 [] 清空"},
        },
    )
    @tools_enabled
    async def rss_set_user_handlers(self, handlers_json: str = "", **kwargs: Any) -> dict[str, Any]:
        user_id = str(kwargs.get("user_id") or "")
        try:
            handlers = json.loads(handlers_json) if handlers_json.strip() else []
        except ValueError as exc:
            return {"success": False, "content": f"JSON 解析失败：{exc}"}
        if not isinstance(handlers, list):
            return {"success": False, "content": "handlers_json 必须是数组"}
        self.store.repos.users.get_or_create(user_id)
        self.store.repos.users.update(user_id, handlers=handlers)
        return {"success": True, "content": f"你的默认处理链已更新为 {len(handlers)} 个 handler"}

    # ── 直接推一条已有条目（供模型复述用） ──────────────────────

    @Tool(
        "rss_rewrite_entry",
        description="让内容处理链重写一条文本（调试 handler 链路时使用）",
        parameters={
            "title": {"type": "string", "description": "标题"},
            "content": {"type": "string", "description": "正文"},
            "handlers_json": {
                "type": "string",
                "description": "handler 数组 JSON，留空用当前用户默认链",
                "required": False,
            },
        },
    )
    @tools_enabled
    async def rss_rewrite_entry(
        self, title: str = "", content: str = "", handlers_json: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        handlers: list[dict[str, Any]] = []
        if handlers_json.strip():
            try:
                parsed = json.loads(handlers_json)
            except ValueError as exc:
                return {"success": False, "content": f"JSON 解析失败：{exc}"}
            if isinstance(parsed, list):
                handlers = parsed
        else:
            user = self.store.repos.users.get(str(kwargs.get("user_id") or ""))
            handlers = list(user.handlers) if user else []

        context = HandlerContext(title=title, content=content)
        outcome = await self.handler_chain.run(handlers, context)
        return {
            "success": True,
            "content": json.dumps(
                {
                    "allow": outcome.allow,
                    "title": outcome.context.title,
                    "content": outcome.context.content,
                    "trace": outcome.trace,
                },
                ensure_ascii=False,
                indent=2,
            ),
        }
