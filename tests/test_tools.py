"""工具面测试：XML 条目直推与音视频附件。"""

from __future__ import annotations

import pytest

from rsshub.application.services.subscription_service import SubscriptionService
from rsshub.application.tools.rss_tools import RssToolsMixin
from rsshub.infrastructure.persistence.store import Store
from tests.support import FakeClient, StubCtx, make_config

MP3_URL = "https://example.com/ep.mp3"
MP3 = b"ID3" + b"0" * 64
XML = (
    "<title>播客更新</title><link>https://example.com/post/2</link>"
    f'<enclosure url="{MP3_URL}" type="audio/mpeg" length="67" />'
)


class Harness(RssToolsMixin):
    def __init__(self, ctx: StubCtx, store: Store, config: object, client: FakeClient) -> None:
        from rsshub.application.services.polling import build_defaults

        self.ctx = ctx
        self.store = store
        self.config = config
        self.defaults = build_defaults(config)
        self.sub_service = SubscriptionService(ctx, store, config, client)
        self._client = client
        self.handler_chain = None


def _harness(tmp_path: object) -> Harness:
    ctx = StubCtx(tmp_path)  # type: ignore[arg-type]
    store = Store.open(tmp_path / "rsshub.db")  # type: ignore[operator]
    client = FakeClient(payloads={MP3_URL: MP3})
    return Harness(ctx, store, make_config(), client)


@pytest.mark.asyncio
async def test_xml_push_attaches_audio_file(tmp_path: object) -> None:
    harness = _harness(tmp_path)

    result = await harness.rss_push_xml_entry(
        source_key="podcast", title="播客更新", xml=XML, stream_id="s1", user_id="u1"
    )

    assert result["success"] is True
    assert result["structured_content"]["attachments"] == 1
    assert harness.ctx.files.sent
    assert "音视频：" not in harness.ctx.send.sent[-1][1]
    assert harness.store.repos.history.list_by_session("s1")[0].source_type == "agent"


@pytest.mark.asyncio
async def test_xml_push_falls_back_to_link_without_scope(tmp_path: object) -> None:
    harness = _harness(tmp_path)
    harness.ctx.files.fail_with = PermissionError("文件凭证无效或已过期")

    result = await harness.rss_push_xml_entry(
        source_key="podcast", title="播客更新", xml=XML, stream_id="s1", user_id="u1"
    )

    assert result["success"] is True
    assert result["structured_content"]["attachments"] == 0
    assert f"音视频：{MP3_URL}" in harness.ctx.send.sent[-1][1]


@pytest.mark.asyncio
async def test_xml_push_dedupes_same_entry(tmp_path: object) -> None:
    harness = _harness(tmp_path)

    await harness.rss_push_xml_entry(source_key="podcast", title="播客更新", xml=XML, stream_id="s1")
    second = await harness.rss_push_xml_entry(source_key="podcast", title="播客更新", xml=XML, stream_id="s1")

    assert second["success"] is True
    assert "已经推送过" in second["content"]
