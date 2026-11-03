# RSSHub 订阅推送（MaiBot 插件）

把 RSS/Atom/JSON Feed 订阅接入麦麦：定时轮询、去重推送、人格化播报，并在群里直接用自然语言管理订阅。

本插件自 AstrBot 插件 [astrbot_plugin_rsshub](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub)（作者 FlanChanXwO）移植并重设计，遵循 AGPL-3.0。

> **提示：宿主要开「文件功能」开关。**
>
> 音频与视频要按原文件发出，必须在宿主设置里打开 **设置 → 能力 → 文件功能**（配置文件里的 `[capabilities] files = true`，宿主默认关闭）。开关关着时插件照常推送，只是音视频改为在正文末尾附链接；定时推送无论开关如何都走链接，因为宿主的文件凭证只发给模型工具调用。
>
> 打开后宿主会同时启用 5 个文件工具（列附件、下载、读文本、存文本、发文件）、插件 SDK 的文件能力与 WebUI 文件入口；存储上限按宿主 `[files]` 分区（默认单文件 50 MiB、配额 10 GiB）。

## 功能

- **订阅管理**：RSS/Atom/JSON Feed 全格式，15 个 LLM 工具让麦麦直接帮你订阅、退订、改配置
- **定时轮询**：插件自管 asyncio 调度，失败重试与推送历史审计，重启不重复推送
- **三种推送模式**：人格化播报（麦麦用自己的口吻分享）／模板直推／注入上下文（麦麦自己决定说不说，图也一并进上下文，它可以自己决定要不要把图发出来）
- **四级配置继承**：订阅源 → 会话默认 → 用户默认 → 全局默认，逐层覆盖；订阅级间隔填 0 表示回落到上级
- **内容处理链**：`ai_filter` 过滤、`ai_transform` 改写，按订阅源或用户挂载
- **推送历史**：每次推送按条目落库，失败逐条重试，可按会话查询；成功记录同时用作重复推送的判据
- **富媒体**：正文图与封面图随消息发出，多图合并转发，表格渲染成图片；音视频在工具推送里按原文件发送，定时推送附链接
- **WebUI 管理台**：在麦麦 WebUI 侧栏管理订阅、改推送参数、看推送历史与批量导入；停用的订阅留在列表里可随时启用，时间按本地时区显示

## 安装

宿主要求：MaiBot 1.3.19–1.3.99，`maibot-plugin-sdk` 2.21.0–2.21.99（与 `_manifest.json` 声明一致）。

音视频文件发送还要求宿主打开「文件功能」开关，见上文提示；未开启时工具推送会检测到文件服务不可用，自动退回链接行。

把仓库放到宿主 `plugins/` 目录（目录名 `omsociety_rsshub`），或通过插件中心安装。宿主按 `_manifest.json` 的 `dependencies` 自动安装 Python 依赖。

首次加载后插件在 WebUI 的「插件配置」里生成配置项；订阅数据存在宿主给插件分配的 `data_dir` 下，不写宿主目录。

## 配置

配置分两层：

- **宿主 `config.toml`**（全局，WebUI 的插件配置页分组显示）：插件、抓取与轮询、网络与代理、推送、人格化播报、富媒体与附件、RSSHub 实例
- **SQLite**（`data_dir`）：订阅源、用户默认、会话默认、推送历史

时间类配置一律用「分钟」或「秒」，体积类一律用「MiB」，字段标签里直接带单位，字段描述写明调大调小的后果。详见仓库内 `config.example.toml` 的注释。

### 工具开关

`[plugin] tools_enabled`（插件配置页第一分区「向麦麦提供订阅工具」）决定是否把 15 个 LLM 工具注册给麦麦：

- 打开（默认）：麦麦可以直接用自然语言订阅、改配置、查历史；15 个工具会占用一部分上下文。
- 关闭：麦麦看不到这些工具，只能用 `/订阅` 等命令或 WebUI 页面管理；聊天命令与页面不受影响。

宿主只在插件注册时收集组件，所以切换后要**重新加载插件**才彻底生效：在插件配置页把「启用」关掉再打开，或重启宿主。重载前工具仍留在麦麦的工具表里，但调用会被工具本身拒绝并提示已关闭。

### 推送模式

| 模式 | 行为 |
|---|---|
| `persona` | 把条目交给麦麦的人格化生成，按群聊口吻播报；生成失败自动回退直推 |
| `direct` | 模板化图文直接推送，不经过 LLM |
| `silent` | 把条目注入聊天上下文（不直接发言），并固定请麦麦主动思考一轮要不要分享；有新图时压到 1 MiB 一并注入，麦麦可以在自己的回复里把图一起发出来 |

## 命令

| 命令（中英等价） | 说明 |
|---|---|
| `/订阅` 或 `/sub <URL> [标题]` | 订阅（支持 RSSHub 短路由，如 `/github/trending`）；可选自定义标题 |
| `/退订` 或 `/unsub [编号]` | 退订，不传编号清空当前会话 |
| `/列表` 或 `/sub_list` | 列出当前会话订阅 |
| `/状态` 或 `/sub_status` | 查看轮询与近期推送概况 |
| `/测试推送 [编号]` 或 `/sub_test [编号]` | 立即把某条订阅的最新 3 条内容推到当前会话，验证源、处理链与推送效果；需要授权，不写推送历史、不影响定时轮询 |
| `/帮助` 或 `/rsshelp` | 帮助 |

`/测试推送` 走宿主的 operator 权限校验，默认谁都不能用。两种授权方式：

```toml
# 方式一：加入插件管理权限（同时拥有安装/停用插件等权限）
[plugin]
permission = ["qq:你的QQ号"]

# 方式二：只放行这一条命令
[plugin.command_permissions."omsociety.rsshub.rss_test"]
allow_users = ["qq:你的QQ号"]
```

保存后宿主热重载配置，无需重启。

## LLM 工具

| 工具 | 用途 |
|---|---|
| `rss_subscribe` | 订阅新源 |
| `rss_unsubscribe` | 退订 |
| `rss_unsubscribe_all` | 退订当前会话全部 |
| `rss_list_subscriptions` | 列出订阅 |
| `rss_push_xml_entry` | 手动推送指定条目 |
| `rss_list_push_history` | 查询推送历史 |
| `rss_list_handlers` | 列出可用内容处理器 |
| `rss_get_handlers` | 查看当前生效的处理器 |
| `rss_set_subscription_handlers` | 设置订阅源处理器 |
| `rss_set_user_handlers` | 设置用户默认处理器 |
| `rss_set_subscription_option` | 修改订阅源选项 |
| `rss_set_user_default_option` | 修改用户默认选项 |
| `rss_set_session_default_option` | 设置会话默认选项 |
| `rss_get_session_defaults` | 查看会话默认配置 |
| `rss_rewrite_entry` | 用处理链试跑一段内容（调试） |

## 能力与契约事实

本插件按宿主能力设计，以下是使用时会碰到的几条契约：

- **音视频与文件**：宿主只为模型工具调用签发文件发送凭证，因此 `rss_push_xml_entry` 推送的音视频按原文件发送；后台定时推送没有凭证，音频与视频在正文末尾附一行原始链接。
- **订阅地址**：直接填 Feed 地址或 RSSHub 路由（以 `/` 开头，按 `rsshub.base_url` 拼接），内置 RSSHub 路由检索不在插件里，路由需要自己填。
- **管理台**：宿主 WebUI 的插件页面，覆盖订阅管理、推送历史、参数调整与批量导入。
- **数据**：订阅与推送历史存在插件自己的 SQLite 库（`data_dir/rsshub.db`），换环境时随 `data_dir` 一起迁移。
- **调度**：宿主没有 cron 能力，插件自带 asyncio 调度循环，重启后按 `next_check_at` 续跑。
- **耗时约束**：宿主对一次插件调用有 60 秒上限，图片下载按「单图 `media.image_timeout_seconds`（默认 20 秒，5–120 可调）、整批 1.5 倍、最多 3 张并发」设预算，超预算的图跳过；每次推送会记一行 `media=` 与 `send=` 耗时，附图被跳过时另记一行跳过原因。
- **注入带图**：注入模式的图片先压到 1 MiB 以内再进上下文（宿主单帧 16 MiB，原图直送会占掉大半配额），压不下去的图跳过；注入消息另有不超过 12 字符的消息 ID，提示行里给出这个 ID 与图片序号，麦麦据此在自己的回复里取图，发不发由它决定。图没取到时注入文本会写明原因，`/测试推送` 的回复也会带上「附图没取到：N 张（原因）」。
- **大图**：`media.image_max_mib` 是下载门槛（1–50），超过就跳过；直接发送的推送里，超过 8 MiB 的原图会先压到 8 MiB 以内再发，留在宿主单帧上限之内。
- **推送模式取值**：`push_mode` 只认 `persona`/`direct`/`silent`，其它值按 `direct` 处理。
- **历史保留**：推送历史默认保留 30 天，调度器每天清理一次；没有任何订阅、也没有推送记录的订阅源行一并清理。
- **术语**：文档里的「订阅」指你为某个源建立的那条订阅记录，「订阅源」指被订阅的 Feed / RSSHub 路由本身。

## 开发

```bash
pip install -r requirements.txt -r requirements-dev.txt "maibot-plugin-sdk==2.21.0"
python -m pytest -q
ruff format --check . && ruff check .
python tools/check_webui.py .
python tools/check_agents_md.py --root .
```

测试宿主环境与生产实例分离；联调时把本仓库目录拷到宿主 `plugins/omsociety_rsshub` 后热重载。

`webui.json` 声明 WebUI 页面，`webui/index.html` 是页面本体（单文件、脚本与样式内联）。改 HTML 后在 WebUI 里点「重载页面」即可生效；改 API 声明或页面绑定需要重载插件。

## 许可与致谢

- 本插件：AGPL-3.0
- 源自 [astrbot_plugin_rsshub](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub)，原作者 FlanChanXwO，同为 AGPL-3.0
