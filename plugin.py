"""RSSHub 订阅推送 —— MaiBot 插件。

自 AstrBot 插件 astrbot_plugin_rsshub（作者 FlanChanXwO，AGPL-3.0）移植并重设计：
https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub
衍生作品，遵循 AGPL-3.0 许可。

组件：15 个 @Tool（LLM 主力交互）+ 6 个 @Command（高频命令）+ 8 个 WebUI @API。
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
from .rsshub.infrastructure.fetcher.client import HttpClient, socksio_available
from .rsshub.infrastructure.persistence.store import Store
from .rsshub.interfaces.web_api import WebApiMixin

# ────────────────────────────── 配置模型 ──────────────────────────────


class PluginSection(PluginConfigBase):
    """插件总开关。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    tools_enabled: bool = Field(
        default=True,
        description="是否把 15 个订阅工具提供给麦麦调用",
        json_schema_extra={
            "label": "向麦麦提供订阅工具",
            "hint": "关闭后麦麦不再调用订阅工具，只能用 /订阅 等命令操作；切换后需重载插件才从工具列表移除",
        },
    )
    config_version: str = Field(
        default="1.4.0",
        description="配置版本号，请勿手动修改",
        json_schema_extra={"label": "配置版本", "hidden": True},
    )


class PollingSection(PluginConfigBase):
    """抓取节奏：多久抓一次、一次抓几个源、单源最多推几条。"""

    __ui_label__ = "抓取与轮询"
    __ui_icon__ = "refresh-cw"
    __ui_order__ = 1

    interval_minutes: int = Field(
        default=10,
        ge=1,
        le=1440,
        description="所有订阅源默认多久抓取一次，单位分钟",
        json_schema_extra={
            "label": "全局轮询间隔（分钟）",
            "hint": "订阅级 interval 会覆盖它；间隔越短越及时，但更容易被站点限流",
        },
    )
    concurrency: int = Field(
        default=5,
        ge=1,
        le=20,
        description="同一轮里同时抓取的订阅源数量",
        json_schema_extra={"label": "同时抓取几个源", "hint": "源多时适当调大；被限流就调小"},
    )
    request_timeout: float = Field(
        default=30.0,
        ge=5.0,
        le=120.0,
        description="单个订阅源一次请求的超时秒数",
        json_schema_extra={"label": "单源请求超时（秒）", "hint": "慢源经常超时就调大，如 60"},
    )
    max_entries_per_poll: int = Field(
        default=5,
        ge=1,
        le=50,
        description="每个订阅源一轮最多推送的新条目数",
        json_schema_extra={
            "label": "每源每轮最多推送几条",
            "hint": "超出部分本轮丢弃且不补推，只防刷屏用；想一条不落就调到 50",
        },
    )
    user_agent: str = Field(
        default="maibot-plugin-rsshub (+https://github.com/OMSociety/maibot_plugin_rsshub)",
        description="抓取时向站点声明的身份标识，即 HTTP User-Agent 请求头",
        json_schema_extra={
            "label": "抓取标识（User-Agent）",
            "hint": "多数源不看这个；遇到 403 或空内容时改成浏览器的 UA 字符串",
        },
    )


class NetworkSection(PluginConfigBase):
    """网络出口：代理与内网放行。"""

    __ui_label__ = "网络与代理"
    __ui_icon__ = "globe"
    __ui_order__ = 2

    proxy_enabled: bool = Field(
        default=False,
        description="抓取订阅源与下载图片时是否走代理",
        json_schema_extra={"label": "走代理抓取", "hint": "国内源通常不用；抓境外源被墙时打开"},
    )
    proxy_url: str = Field(
        default="http://127.0.0.1:7897",
        description="代理服务器地址，支持 http 与 socks5",
        json_schema_extra={
            "label": "代理地址",
            "placeholder": "http://127.0.0.1:7897 或 socks5://127.0.0.1:7897",
        },
    )
    allow_private_addresses: bool = Field(
        default=False,
        description="允许抓取内网、环回与云元数据地址；这是 SSRF 防护的放行开关",
        json_schema_extra={
            "label": "允许抓内网地址",
            "hint": "只有自建 RSSHub 跑在本机或内网时才打开；开启后群友提交的订阅链接也能访问内网地址",
        },
    )


class PushSection(PluginConfigBase):
    """推送：什么模式、失败怎么办。"""

    __ui_label__ = "推送"
    __ui_icon__ = "send"
    __ui_order__ = 3

    default_mode: Literal["persona", "direct", "silent"] = Field(
        default="persona",
        description="新订阅默认怎么发：人格化播报、原文直推、或注入上下文让麦麦自己决定说不说",
        json_schema_extra={
            "label": "默认推送模式",
            "hint": "persona=麦麦用自己的口吻说；direct=模板原文；silent=注入上下文并请麦麦想一轮要不要开口",
        },
    )
    persona_fallback_to_direct: bool = Field(
        default=True,
        description="人格化生成失败或超时后，改发模板原文",
        json_schema_extra={"label": "生成失败改发原文", "hint": "关掉则这一轮不推送，记入历史等重试"},
    )
    persona_timeout_seconds: int = Field(
        default=30,
        ge=5,
        le=120,
        description="等待人格化生成的最长秒数",
        json_schema_extra={"label": "人格化超时（秒）", "hint": "模型慢时调大；超时会按上面的回退设置处理"},
    )
    max_reference_chars: int = Field(
        default=8000,
        ge=500,
        le=500000,
        description="交给麦麦参考的条目正文最大字符数",
        json_schema_extra={
            "label": "参考正文上限（字符）",
            "hint": "正文越长生成越慢、越容易被细节带偏；8000 约等于两三篇短文",
        },
    )


class PersonaSection(PluginConfigBase):
    """人格化播报的口吻要求。"""

    __ui_label__ = "人格化播报"
    __ui_icon__ = "message-circle"
    __ui_order__ = 4

    format_instruction: str = Field(
        default=(
            "你是群里的熟人，刚看到这些订阅更新，用自己的口吻随口分享给大家。"
            "一两句话点出最有意思的内容，别像新闻播报，别逐条罗列标题，别加引号。"
            "条目链接附在最后。"
        ),
        description="作为格式要求附加给麦麦的播报指令",
        json_schema_extra={
            "label": "播报口吻",
            "rows": 4,
            "hint": "写你想要的效果，比如「一句话吐槽」「像朋友转帖」；不用写内容，内容是自动附上的",
        },
    )


class MediaSection(PluginConfigBase):
    """图片随消息发出；音视频要宿主打开「设置 → 能力 → 文件功能」才能发原件。"""

    # 音视频送达方式取决于调用来源：模型工具调用期间宿主签发文件凭证，可按原文件发送；
    # 后台定时推送没有凭证，只能附链接行。attach_files 只影响工具调用，定时推送不受它控制。

    __ui_label__ = "富媒体与附件"
    __ui_icon__ = "image"
    __ui_order__ = 5

    max_images: int = Field(
        default=3,
        ge=0,
        le=9,
        description="每条推送最多附带的图片张数",
        json_schema_extra={"label": "每条最多几张图", "hint": "0 表示完全不带图"},
    )
    image_max_mib: int = Field(
        default=8,
        ge=1,
        le=20,
        description="单张图片允许下载的大小上限，单位 MiB",
        json_schema_extra={"label": "单张图片上限（MiB）", "hint": "超过的图直接跳过，不影响文字推送"},
    )
    table_render_enabled: bool = Field(
        default=True,
        description="条目正文里的表格渲染成图片再发",
        json_schema_extra={"label": "表格转图片", "hint": "渲染失败会跳过表格，只发文字"},
    )
    multi_image_forward: bool = Field(
        default=True,
        description="多张图时用合并转发发送",
        json_schema_extra={"label": "多图合并转发", "hint": "关闭则文字和图片混排成一条"},
    )
    attach_files: bool = Field(
        default=True,
        description="模型工具推送音视频时按原文件发送",
        json_schema_extra={
            "label": "音视频按文件发送",
            "hint": "需要宿主打开「文件功能」开关；定时推送没有文件凭证，始终附链接",
        },
    )
    max_files: int = Field(
        default=2,
        ge=0,
        le=5,
        description="一次工具推送最多附带的音视频文件个数",
        json_schema_extra={"label": "每次最多几个附件", "hint": "0 表示一律只发链接"},
    )
    file_max_mib: int = Field(
        default=20,
        ge=1,
        le=100,
        description="单个音视频文件允许下载的大小上限，单位 MiB",
        json_schema_extra={"label": "单个附件上限（MiB）", "hint": "超过的附件改为发链接"},
    )


class RsshubSection(PluginConfigBase):
    """只影响 /xxx 这类短路由，直接填 Feed 地址时不经过它。"""

    __ui_label__ = "RSSHub"
    __ui_icon__ = "rss"
    __ui_order__ = 7

    base_url: str = Field(
        default="https://rsshub.app",
        description="拼接在短路由前的 RSSHub 实例地址",
        json_schema_extra={
            "label": "RSSHub 实例地址",
            "placeholder": "https://rsshub.app",
            "hint": "公共实例经常限流，自建实例填自己的地址更稳",
        },
    )


class RsshubPluginConfig(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    polling: PollingSection = Field(default_factory=PollingSection)
    network: NetworkSection = Field(default_factory=NetworkSection)
    push: PushSection = Field(default_factory=PushSection)
    persona: PersonaSection = Field(default_factory=PersonaSection)
    media: MediaSection = Field(default_factory=MediaSection)
    rsshub: RsshubSection = Field(default_factory=RsshubSection)


# ────────────────────────────── 插件主类 ──────────────────────────────


class RsshubPlugin(RssToolsMixin, RssCommandsMixin, WebApiMixin, MaiBotPlugin):
    """插件主类：薄入口，组件实现见 rsshub/。"""

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

    def get_components(self) -> list[dict[str, Any]]:
        """按配置决定是否把工具注册给宿主。

        宿主只在插件注册时收集组件，所以开关改动要重载插件才会反映到工具列表；
        未重载前 ``tools_enabled`` 装饰器仍会让工具直接回绝，避免半可用状态。
        """
        components = super().get_components()
        plugin_cfg = getattr(getattr(self, "config", None), "plugin", None)
        if plugin_cfg is None or bool(getattr(plugin_cfg, "tools_enabled", True)):
            return components
        return [item for item in components if str(item.get("type")) != "TOOL"]

    # ── 生命周期 ────────────────────────────────────────────────

    async def on_load(self) -> None:
        if not self.config.plugin.enabled:
            self.ctx.logger.info("RSSHub 插件在配置中处于关闭状态，跳过启动")
            return
        self._boot()
        self.ctx.logger.info(
            "RSSHub 插件已就绪：数据库 %s，轮询间隔 %s 分钟",
            self._db_path(),
            self.config.polling.interval_minutes,
        )

    async def on_unload(self) -> None:
        await self._teardown()
        self.ctx.logger.info("RSSHub 插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        if scope != CONFIG_RELOAD_SCOPE_SELF:
            return
        enabled = bool(self.config.plugin.enabled)
        self.ctx.logger.info("RSSHub 配置更新到 %s（enabled=%s），重建轮询与推送管线", version, enabled)
        await self._teardown()
        if not enabled:
            self.ctx.logger.info("RSSHub 插件在配置中处于关闭状态，轮询已停止")
            return
        self._boot()

    # ── 装配 ────────────────────────────────────────────────────

    def _db_path(self) -> Path:
        return Path(self.ctx.paths.data_dir) / "rsshub.db"

    def _boot(self) -> None:
        config: Any = self.config
        self.store = Store.open(self._db_path())
        self.defaults = build_defaults(config)
        proxy = str(config.network.proxy_url) if config.network.proxy_enabled else ""
        if proxy.lower().startswith("socks") and not socksio_available():
            self.ctx.logger.error(
                "配置了 socks 代理但依赖 socksio 未安装，本次按直连处理；安装 httpx[socks] 并重载插件后生效"
            )
            proxy = ""
        self._client = HttpClient(
            timeout=float(config.polling.request_timeout),
            user_agent=str(config.polling.user_agent),
            proxy=proxy,
            allow_private=bool(config.network.allow_private_addresses),
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
