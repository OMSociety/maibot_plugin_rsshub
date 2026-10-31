"""内容处理链：ai_filter / ai_transform。

设计口径（沿用源插件 v2 的结论）：
- 链按数组顺序串行执行，单个 handler 抛异常只记 trace 并继续；
- ai_filter 只有显式 JSON `{"allow": false}` 才拦截，其余情况（无提示词、模型失败、
  输出非法）一律放行；
- ai_transform 只做 plaintext 作用域（改写标题/摘要/正文）；xml 作用域依赖源插件的
  XML 校验工具链，本移植不提供，遇到时记 trace 并跳过。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ...domain.entry import Entry

DEFAULT_REASON_MAX_CHARS = 120

_FILTER_PROMPT = (
    "下面的内容来自一个订阅源，将被推送到聊天群。"
    "请判断它是否值得推送（与群主题相关、有信息量、不是广告或无意义的水文）。\n"
    "订阅源：{feed_title}\n标题：{title}\n摘要：{summary}\n正文：{content}\n\n"
    '只输出一行 JSON：{{"allow": true 或 false, "reason": "简要理由"}}'
)

_TRANSFORM_PROMPT = (
    "请改写下面这条订阅内容，使其更适合聊天阅读：保留事实，去掉模板套话与广告，"
    "标题不超过 60 字，摘要不超过 200 字。\n"
    "标题：{title}\n摘要：{summary}\n正文：{content}\n\n"
    '只输出一行 JSON：{{"title": "...", "summary": "...", "content": "..."}}'
)


@dataclass
class HandlerContext:
    """传给 handler 的内容载体。"""

    title: str = ""
    summary: str = ""
    content: str = ""
    link: str = ""
    author: str = ""
    feed_title: str = ""
    feed_link: str = ""
    raw_xml: str = ""
    media_urls: list[str] = field(default_factory=list)
    layout: str = ""

    @classmethod
    def from_entry(cls, entry: Entry, *, feed_title: str = "", feed_link: str = "") -> HandlerContext:
        return cls(
            title=entry.title,
            summary=entry.summary,
            content=entry.content,
            link=entry.link,
            author=entry.author,
            feed_title=feed_title,
            feed_link=feed_link,
            raw_xml=entry.raw_xml,
        )


@dataclass
class HandlerOutcome:
    allow: bool
    context: HandlerContext
    trace: list[dict[str, Any]]


def _render_prompt(template: str, context: HandlerContext) -> str:
    return template.format(
        title=context.title,
        summary=context.summary[:1000],
        content=context.content[:3000],
        link=context.link,
        feed_title=context.feed_title,
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出第一个 JSON 对象。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(stripped[start : end + 1])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class ContentHandlerChain:
    """执行一条订阅的内容处理链。"""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def run(
        self,
        handlers: list[dict[str, Any]],
        context: HandlerContext,
        *,
        session_id: str = "",
    ) -> HandlerOutcome:
        trace: list[dict[str, Any]] = []
        if not handlers:
            return HandlerOutcome(allow=True, context=context, trace=trace)

        current = context
        for spec in handlers:
            handler_type = str(spec.get("type") or spec.get("id") or "")
            status = spec.get("status", 1)
            if status == 0:
                trace.append({"type": handler_type, "status": "disabled"})
                continue
            if handler_type not in {"ai_filter", "ai_transform"}:
                trace.append({"type": handler_type, "status": "skipped", "reason": "未实现的内置 handler"})
                continue

            config = spec.get("config") if isinstance(spec.get("config"), dict) else {}
            try:
                if handler_type == "ai_filter":
                    allow, reason = await self._run_filter(config, current, session_id)
                    trace.append(
                        {"type": handler_type, "status": "ok", "action": "allow" if allow else "deny", "reason": reason}
                    )
                    if not allow:
                        return HandlerOutcome(allow=False, context=current, trace=trace)
                else:
                    current, applied = await self._run_transform(config, current, session_id)
                    trace.append({"type": handler_type, "status": "ok" if applied else "noop"})
            except Exception as exc:
                self._ctx.logger.warning("RSS handler %s 执行失败: %s", handler_type, exc)
                trace.append({"type": handler_type, "status": "error", "reason": str(exc)})
                continue

        return HandlerOutcome(allow=True, context=current, trace=trace)

    async def _generate(self, prompt: str, session_id: str) -> str:
        result = await self._ctx.llm.generate(prompt)
        if not isinstance(result, dict):
            return ""
        if result.get("success") is False:
            return ""
        return str(result.get("response") or "")

    async def _run_filter(self, config: dict[str, Any], context: HandlerContext, session_id: str) -> tuple[bool, str]:
        template = str(config.get("prompt") or _FILTER_PROMPT)
        scope = str(config.get("input_scope") or "text")
        prompt = _render_prompt(template, context)
        if scope == "raw_xml" and context.raw_xml:
            prompt = (
                _render_prompt(template, HandlerContext(raw_xml=context.raw_xml)) + "\n\n原始 XML：\n" + context.raw_xml
            )

        response = await self._generate(prompt, session_id)
        parsed = _parse_json_object(response) if response else None
        if parsed is None or not isinstance(parsed.get("allow"), bool):
            return True, "模型未给出有效判定，默认放行"
        reason = str(parsed.get("reason") or "")
        limit = int(config.get("reason_max_length") or DEFAULT_REASON_MAX_CHARS)
        return bool(parsed["allow"]), reason[:limit]

    async def _run_transform(
        self, config: dict[str, Any], context: HandlerContext, session_id: str
    ) -> tuple[HandlerContext, bool]:
        if str(config.get("scope") or "plaintext") != "plaintext":
            self._ctx.logger.info("RSS ai_transform 的 xml 作用域本移植不支持，跳过该 handler")
            return context, False

        template = str(config.get("prompt") or _TRANSFORM_PROMPT)
        response = await self._generate(_render_prompt(template, context), session_id)
        parsed = _parse_json_object(response) if response else None
        if parsed is None:
            return context, False

        updated = HandlerContext(**vars(context))
        changed = False
        for field_name in ("title", "summary", "content"):
            value = parsed.get(field_name)
            if isinstance(value, str) and value.strip() and value.strip() != getattr(updated, field_name):
                setattr(updated, field_name, value.strip())
                changed = True
        return updated, changed
