"""测试替身：宿主上下文的离线模拟。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


class StubLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def _record(self, level: str, message: str, *args: object) -> None:
        self.messages.append((level, message % args if args else message))

    def info(self, message: str, *args: object) -> None:
        self._record("info", message, *args)

    def warning(self, message: str, *args: object) -> None:
        self._record("warning", message, *args)

    def error(self, message: str, *args: object) -> None:
        self._record("error", message, *args)

    def exception(self, message: str, *args: object) -> None:
        self._record("exception", message, *args)


class StubSend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.hybrid_calls: list[dict] = []
        self.forward_calls: list[dict] = []

    async def text(self, text: str, stream_id: str, **kwargs: object) -> bool:
        self.sent.append((stream_id, text))
        return True

    async def hybrid(self, segments: list[dict], stream_id: str, **kwargs: object) -> bool:
        self.hybrid_calls.append({"stream_id": stream_id, "segments": segments})
        return True

    async def forward(self, messages: list[dict], stream_id: str, **kwargs: object) -> bool:
        self.forward_calls.append({"stream_id": stream_id, "messages": messages})
        return True


class StubCtx:
    """提供 send / logger / paths / llm / maisaka 的最小上下文。"""

    def __init__(self, data_dir: Path, reply_text: str = "顺手分享一条订阅更新") -> None:
        self.logger = StubLogger()
        self.send = StubSend()
        self.paths = SimpleNamespace(data_dir=data_dir, runtime_dir=data_dir / "tmp")
        self.response = reply_text
        self.llm_calls: list[dict] = []
        self.mai_reply_calls: list[dict] = []
        self.mai_append_calls: list[dict] = []
        self.mai_proactive_calls: list[dict] = []
        outer = self

        class _Llm:
            async def generate(self, prompt: str, **kwargs: object) -> dict:
                outer.llm_calls.append({"prompt": prompt, **kwargs})
                return {"success": True, "response": '{"allow": true}', "reasoning": "", "model": "stub"}

        class _Reply:
            async def generate(self, stream_id: str, reference: str, **kwargs: object) -> dict:
                outer.mai_reply_calls.append({"stream_id": stream_id, "reference": reference, **kwargs})
                text = outer.response
                return {"success": bool(text), "text": text, "selected_expression_ids": []}

        class _Context:
            async def append(self, stream_id: str, segments: list[dict], **kwargs: object) -> dict:
                outer.mai_append_calls.append({"stream_id": stream_id, "segments": segments, **kwargs})
                return {"success": True}

        class _Proactive:
            async def trigger(self, stream_id: str, intent: str, **kwargs: object) -> dict:
                outer.mai_proactive_calls.append({"stream_id": stream_id, "intent": intent, **kwargs})
                return {"success": True}

        class _Render:
            async def html2png(self, html: str, **kwargs: object) -> dict:
                outer.render_calls.append({"html": html, **kwargs})
                return {"image_base64": outer.render_payload, "mime_type": "image/png"}

        self.render_payload = "dGFibGU="
        self.render_calls: list[dict] = []
        self.llm = _Llm()
        self.render = _Render()
        self.maisaka = SimpleNamespace(reply=_Reply(), context=_Context(), proactive=_Proactive())
        self.files = StubFiles()


class StubFiles:
    """文件能力桩：记录上传与发送，可注入失败（模拟无凭证时的 PermissionError）。"""

    def __init__(self) -> None:
        self.uploads: list[dict] = []
        self.sent: list[str] = []
        self.fail_with: Exception | None = None

    async def upload(self, path: object, name: str = "", mime_type: str = "") -> dict:
        if self.fail_with is not None:
            raise self.fail_with
        self.uploads.append({"path": path, "name": name, "mime_type": mime_type})
        return {"file_ref": f"file://{name}", "max_bytes": 32 * 1024 * 1024}

    async def send(self, file_ref: str) -> dict:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(file_ref)
        return {"sent": True, "message_id": len(self.sent)}


class FakeClient:
    """替代 HttpClient：返回固定 feed 内容，便于离线测试。"""

    def __init__(
        self,
        content: bytes = b"",
        url: str = "https://example.com/feed.xml",
        status: int = 200,
        headers: dict[str, str] | None = None,
        payloads: dict[str, bytes] | None = None,
    ) -> None:
        self.content = content
        self.url = url
        self.status = status
        self.headers = headers or {}
        self.payloads = payloads or {}
        self.calls: list[dict] = []

    async def fetch(self, url: str, etag: str | None = None, last_modified: object = None) -> object:
        from rsshub.infrastructure.fetcher.client import FetchResult

        self.calls.append({"url": url, "etag": etag, "last_modified": last_modified})
        if url in self.payloads:
            return FetchResult(
                url=url, status=200, content=self.payloads[url], headers=self.headers, not_modified=False
            )
        return FetchResult(
            url=self.url, status=self.status, content=self.content, headers=self.headers, not_modified=False
        )

    async def close(self) -> None:
        return None


def make_config(**overrides: object) -> SimpleNamespace:
    """构造与服务层字段对齐的配置快照。"""
    config = SimpleNamespace(
        plugin=SimpleNamespace(enabled=True, config_version="1.0.0"),
        polling=SimpleNamespace(
            interval_minutes=10,
            concurrency=3,
            request_timeout=15.0,
            max_entries_per_poll=3,
            user_agent="test-agent",
        ),
        network=SimpleNamespace(proxy_enabled=False, proxy_url="", allow_private_addresses=True),
        push=SimpleNamespace(
            default_mode="persona",
            persona_fallback_to_direct=True,
            persona_timeout_seconds=5,
            max_reference_chars=2000,
        ),
        persona=SimpleNamespace(format_instruction="随口分享"),
        silent=SimpleNamespace(proactive_trigger=False),
        media=SimpleNamespace(
            max_images=3,
            image_max_mib=1,
            table_render_enabled=True,
            multi_image_forward=True,
            attach_files=True,
            max_files=2,
            file_max_mib=20,
        ),
        rsshub=SimpleNamespace(base_url="https://rsshub.app"),
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config
