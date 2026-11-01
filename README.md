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
- **三种推送模式**：人格化播报（麦麦用自己的口吻分享）／模板直推／静默注入上下文
- **三级配置继承**：订阅源 → 用户默认 → 全局默认，逐层覆盖；会话默认另行保存
- **内容处理链**：`ai_filter` 过滤、`ai_transform` 改写，按订阅源或用户挂载
- **推送历史**：每次推送落库，失败自动重试，可按会话查询
- **富媒体**：正文图与封面图随消息发出，多图合并转发，表格渲染成图片；音视频在工具推送里按原文件发送，定时推送附链接
- **WebUI 管理台**：在麦麦 WebUI 侧栏管理订阅、改推送参数、看推送历史与批量导入

## 安装

宿主要求：MaiBot 1.3.19–1.3.99，`maibot-plugin-sdk` 2.21.0–2.21.99（与 `_manifest.json` 声明一致）。

音视频文件发送还要求宿主打开「文件功能」开关，见上文提示；未开启时工具推送会检测到文件服务不可用，自动退回链接行。

把仓库放到宿主 `plugins/` 目录（目录名 `omsociety_rsshub`），或通过插件中心安装。宿主按 `_manifest.json` 的 `dependencies` 自动安装 Python 依赖。

首次加载后插件在 WebUI 的「插件配置」里生成配置项；订阅数据存在宿主给插件分配的 `data_dir` 下，不写宿主目录。

## 配置

配置分两层：

- **宿主 `config.toml`**（全局）：轮询间隔、并发、代理、默认推送模式、人格化口吻、富媒体上限、RSSHub 实例地址
- **SQLite**（`data_dir`）：订阅源、用户默认、会话默认、推送历史

详见仓库内 `config.example.toml` 的注释。

### 推送模式

| 模式 | 行为 |
|---|---|
| `persona` | 把条目交给麦麦的人格化生成，按群聊口吻播报；生成失败自动回退直推 |
| `direct` | 模板化图文直接推送，不经过 LLM |
| `silent` | 只把条目注入聊天上下文，不主动发言；可选让麦麦主动思考一轮 |

## 命令

| 命令 | 说明 |
|---|---|
| `/sub <URL>` | 订阅（支持 RSSHub 短路由，如 `/github/trending`） |
| `/unsub <编号或URL>` | 退订 |
| `/sub_list` | 列出当前会话订阅 |
| `/sub_status` | 查看轮询与近期推送概况 |
| `/rsshelp` | 帮助 |

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

## 与原插件的差异

本插件是重设计而非逐行搬运，以下能力在原插件存在、在这里按宿主能力做了取舍：

- **音视频与文件**：宿主只为模型工具调用签发文件发送凭证，因此 `rss_push_xml_entry` 推送的音视频按原文件发送；后台定时推送没有凭证，音频与视频在正文末尾附一行原始链接。
- **RSSHub Routes 知识库**：原插件内置的 RSSHub 路由检索未移植，订阅时直接填路由即可，宿主侧也没有可供写入的知识库。
- **管理台**：由 AstrBot 的 Plugin Pages 改为宿主 WebUI 的插件页面，功能覆盖订阅管理、推送历史、参数调整与批量导入。
- **数据迁移**：不读取原插件的数据库，需要在麦麦里重新订阅。
- **调度**：宿主没有 cron 能力，插件自带 asyncio 调度循环，重启后按 `next_check_at` 续跑。

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
