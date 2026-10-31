"""RSSHub 订阅推送 —— MaiBot 插件。

自 AstrBot 插件 astrbot_plugin_rsshub（作者 FlanChanXwO，AGPL-3.0）移植并重设计：
https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub
衍生作品，遵循 AGPL-3.0 许可。

组件：15 个 @Tool（LLM 主力交互）+ 5 个 @Command（高频命令）+ 8 个 WebUI @API。
逻辑下沉在 rsshub/ 各模块，本文件只做组件声明与薄转发。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from maibot_sdk import (
    CONFIG_RELOAD_SCOPE_SELF,
    Field,
    MaiBotPlugin,
    PluginConfigBase,
)

from .rsshub.application.commands.rss_commands import RssCommandsMixin
from .rsshub.application.services.content_handler import ContentHandlerChain
from .rsshub.application.services.dispatcher import Dispatcher
from .rsshub.application.services.polling import FeedPoller, build_defaults
from .rsshub.application.services.scheduler import Scheduler
from .rsshub.application.services.subscription_service import SubscriptionService
from .rsshub.application.tools.rss_tools import RssToolsMixin
from .rsshub.domain.settings import GlobalDefaults
from .rsshub.infrastructure.fetcher.client import HttpClient
from .rsshub.infrastructure.persistence.store import Store
from .rsshub.interfaces.web_api import WebApiMixin

# ────────────────────────────── 配置模型 ──────────────────────────────


class PluginSection(PluginConfigBase):
    """插件总开关。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(
        default="1.0.0",
        description="配置版本号，请勿手动修改",
        json_schema_extra={"label": "配置版本", "hidden": True},
    )


class PollingSection(PluginConfigBase):
    """RSS 轮询调度。"""

    __ui_label__ = "轮询调度"
    __ui_order__ = 1

    interval_seconds: int = Field(
        default=600,
        ge=60,
        le=86400,
        description="全局轮询间隔（秒）",
        json_schema_extra={"label": "轮询间隔", "hint": "所有订阅源共用；单源间隔在订阅配置中覆盖"},
    )
    concurrency: int = Field(
        default=5,
        ge=1,
        le=20,
        description="同时抓取的订阅源数量上限",
        json_schema_extra={"label": "抓取并发数"},
    )
    request_timeout: float = Field(
        default=30.0,
        ge=5.0,
        le=120.0,
        description="单个订阅源 HTTP 请求超时（秒）",
        json_schema_extra={"label": "请求超时"},
    )
    max_entries_per_poll: int = Field(
        default=5,
        ge=1,
        le=50,
        description="单次轮询每个订阅源最多推送的新条目数",
        json_schema_extra={"label": "单轮推送上限", "hint": "超出部分丢弃，防止刷屏"},
    )
    user_agent: str = Field(
        default="maibot-plugin-rsshub/0.1 (+https://github.com/OMSociety/maibot_plugin_rsshub)",
        description="抓取订阅源时使用的 User-Agent",
        json_schema_extra={"label": "User-Agent"},
    )


class NetworkSection(PluginConfigBase):
    """网络与代理。"""

    __ui_label__ = "网络代理"
    __ui_order__ = 2

    proxy_enabled: bool = Field(
        default=False,
        description="抓取订阅源时是否走代理",
        json_schema_extra={"label": "启用代理"},
    )
    proxy_url: str = Field(
        default="http://127.0.0.1:7897",
        description="HTTP/SOCKS5 代理地址",
        json_schema_extra={"label": "代理地址", "placeholder": "http://127.0.0.1:7897 或 socks5://127.0.0.1:7897"},
    )
    allow_private_addresses: bool = Field(
        default=False,
        description="允许订阅内网/环回/云元数据地址（SSRF 放行开关）",
        json_schema_extra={
            "label": "放行内网地址",
            "hint": "开启后插件可抓取 127.0.0.0/8、10.0.0.0/8、192.168.0.0/16 等地址，仅在自建 RSSHub 于内网时开启",
        },
    )


class PushSection(PluginConfigBase):
    """推送行为。"""

    __ui_label__ = "推送"
    __ui_order__ = 3

    default_mode: Literal["persona", "direct", "silent"] = Field(
        default="persona",
        description="订阅源的默认推送模式；订阅/用户/会话级配置可覆盖",
        json_schema_extra={
            "label": "默认推送模式",
            "hint": "persona=麦麦人格化播报；direct=模板原文直推；silent=静默注入上下文，不主动说话",
        },
    )
    persona_fallback_to_direct: bool = Field(
        default=True,
        description="人格化生成失败或超时时回退为原文直推",
        json_schema_extra={"label": "失败回退直推"},
    )
    persona_timeout_ms: int = Field(
        default=30000,
        ge=5000,
        le=120000,
        description="人格化生成的超时时间（毫秒）",
        json_schema_extra={"label": "人格化超时"},
    )
    max_reference_chars: int = Field(
        default=8000,
        ge=500,
        le=500000,
        description="送给人格化生成的条目正文最大字符数",
        json_schema_extra={"label": "正文长度上限", "hint": "过长会被订阅源正文拖慢生成并稀释质量"},
    )
    batch_merge: bool = Field(
        default=True,
        description="同一轮询周期同一订阅源的多条新条目合并为一次人格化生成",
        json_schema_extra={"label": "同周期合并生成"},
    )


class PersonaSection(PluginConfigBase):
    """人格化播报提示词。"""

    __ui_label__ = "人格化"
    __ui_order__ = 4

    format_instruction: str = Field(
        default=(
            "你是群里的熟人，刚看到这些订阅更新，用自己的口吻随口分享给大家。"
            "一两句话点出最有意思的内容，别像新闻播报，别逐条罗列标题，别加引号。"
            "条目链接附在最后。"
        ),
        description="人格化生成时附加给麦麦的格式指令",
        json_schema_extra={"label": "播报口吻", "rows": 4},
    )


class SilentSection(PluginConfigBase):
    """静默注入。"""

    __ui_label__ = "静默模式"
    __ui_order__ = 5

    proactive_trigger: bool = Field(
        default=False,
        description="静默注入后是否请求麦麦主动思考一轮（不保证开口，重要订阅不建议用 silent）",
        json_schema_extra={"label": "注入后主动思考"},
    )


class MediaSection(PluginConfigBase):
    """富媒体与降级。

    注意：video_mode/audio_mode 当前固定为 link_card 降级形态——本 SDK 基线（2.16.0）
    没有视频/音频/文件发送通道。SDK 2.17.0 引入 ctx.files.send 后应新增 files 模式
    并移除 link_card 降级实现（见 src/application/services/dispatcher.py 与
    src/infrastructure/media/downloader.py 中的 REMOVAL 注释），届时本字段才可放开
    枚举取值；宿主未升级前保持现状。
    """

    __ui_label__ = "富媒体"
    __ui_order__ = 6

    max_images: int = Field(
        default=3,
        ge=0,
        le=9,
        description="单条推送最多附带的图片数量，0 表示不附图",
        json_schema_extra={"label": "图片上限"},
    )
    image_max_bytes: int = Field(
        default=8388608,
        ge=1048576,
        le=20971520,
        description="单张图片下载大小上限（字节），超限跳过该图",
        json_schema_extra={"label": "图片大小上限"},
    )
    table_render_enabled: bool = Field(
        default=True,
        description="条目正文包含表格时渲染为图片（走宿主 render.html2png）",
        json_schema_extra={"label": "表格转图片"},
    )
    multi_image_forward: bool = Field(
        default=True,
        description="多图时以合并转发形式发送，false 则图文混排在一条消息里",
        json_schema_extra={"label": "多图合并转发"},
    )


class RsshubSection(PluginConfigBase):
    """RSSHub 实例。"""

    __ui_label__ = "RSSHub"
    __ui_order__ = 7

    base_url: str = Field(
        default="https://rsshub.app",
        description="RSSHub 实例地址，订阅 /xxx 类路由时拼接该前缀",
        json_schema_extra={"label": "实例地址", "placeholder": "https://rsshub.app"},
    )


class RsshubPluginConfig(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    polling: PollingSection = Field(default_factory=PollingSection)
    network: NetworkSection = Field(default_factory=NetworkSection)
    push: PushSection = Field(default_factory=PushSection)
    persona: PersonaSection = Field(default_factory=PersonaSection)
    silent: SilentSection = Field(default_factory=SilentSection)
    media: MediaSection = Field(default_factory=MediaSection)
    rsshub: RsshubSection = Field(default_factory=RsshubSection)


# ────────────────────────────── 插件主类 ──────────────────────────────


class RsshubPlugin(RssToolsMixin, RssCommandsMixin, WebApiMixin, MaiBotPlugin):
    """插件主类：薄入口，组件实现见 src/。"""

    config_model = RsshubPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self.store: Store | None = None
        self.defaults: GlobalDefaults = GlobalDefaults()
        self.handler_chain: ContentHandlerChain | None = None
        self.sub_service: SubscriptionService | None = None
        self._client: HttpClient | None = None
        self._poller: FeedPoller | None = None
        self._dispatcher: Dispatcher | None = None
        self._scheduler: Scheduler | None = None

    # ── 生命周期 ────────────────────────────────────────────────

    async def on_load(self) -> None:
        if not self.config.plugin.enabled:
            self.ctx.logger.info("RSSHub 插件在配置中处于关闭状态，跳过启动")
            return
        self._boot()
        self.ctx.logger.info(
            "RSSHub 插件已就绪：数据库 %s，轮询间隔 %ss",
            self._db_path(),
            self.config.polling.interval_seconds,
        )

    async def on_unload(self) -> None:
        await self._teardown()
        self.ctx.logger.info("RSSHub 插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        if scope != CONFIG_RELOAD_SCOPE_SELF:
            return
        self.ctx.logger.info("RSSHub 配置更新到 %s，重建轮询与推送管线", version)
        if self.store is None:
            return
        await self._teardown()
        self._boot()

    # ── 装配 ────────────────────────────────────────────────────

    def _db_path(self) -> Path:
        return Path(self.ctx.paths.data_dir) / "rsshub.db"

    def _boot(self) -> None:
        config: Any = self.config
        self.store = Store.open(self._db_path())
        self.defaults = build_defaults(config)
        proxy = str(config.network.proxy_url) if config.network.proxy_enabled else ""
        self._client = HttpClient(
            timeout=float(config.polling.request_timeout),
            user_agent=str(config.polling.user_agent),
            proxy=proxy,
        )
        self.handler_chain = ContentHandlerChain(self.ctx)
        self._dispatcher = Dispatcher(self.ctx, self.store, config, self._client)
        self._poller = FeedPoller(self.ctx, self.store, config, self._client, self.handler_chain, self._dispatcher)
        self.sub_service = SubscriptionService(self.ctx, self.store, config, self._client)
        self._scheduler = Scheduler(self.ctx, self.store, config, self._poller, self._dispatcher)
        self._scheduler.start()

    async def _teardown(self) -> None:
        if self._scheduler is not None:
            await self._scheduler.stop()
            self._scheduler = None
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self.store is not None:
            self.store.close()
            self.store = None


def create_plugin() -> MaiBotPlugin:
    return RsshubPlugin()
