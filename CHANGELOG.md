# Changelog

本项目的更改记录在此文件。

All notable changes to this project are documented in this file.

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### 新增

- 配置项 `media.image_timeout_seconds`（5–60 秒，默认 20）：单张图片的下载超时改为可调，整批按 1.5 倍放宽；图片站的原图常有 10 MB 以上，原先固定的 8 秒在慢速链路上会把图全部跳过

### 修复

- 附图被跳过时写明原因（下载超时 / 遇到的上限 / 站点没正常返回 / 图片无法解码），日志、`/测试推送` 的回复与注入模式的提示都带上这句，麦麦不再把「没有图」解释成插件没实现取图
- `/测试推送` 的媒体阶段最多等 40 秒：命令本身走宿主的 60 秒调用上限，此前按配置的整批预算等待会把命令拖到超时
- `media.image_max_mib` 的上限从 20 放宽到 50；原图超过 8 MiB 时先压到 8 MiB 以内再发送，留在宿主单条消息上限之内

### Added

- New `media.image_timeout_seconds` setting (5–60 s, default 20): the per-image download timeout is now configurable, with the batch budget set to 1.5× it. Originals on image sites often exceed 10 MB, so the former fixed 8 s skipped every image on a slow link

### Fixed

- Skipped attachments now carry a reason (download timed out / over a limit / site did not answer / not decodable as an image), surfaced in logs, in the `/测试推送` reply and in the injection notice, so the bot no longer explains a missing image as "the plugin cannot fetch images"
- The media phase of `/测试推送` waits at most 40 s: the command itself runs under the host's 60 s invocation limit, so waiting for the full configured budget used to time the command out
- `media.image_max_mib` now accepts up to 50 (was 20); originals above 8 MiB are compressed below 8 MiB before sending, staying within the host's per-message limit

## [0.8.0] - 2026-11-03

### 新增

- WebUI 订阅列表提供已有会话下拉快选，批量导入不必手敲宿主内部的会话 ID；命中已知会话时，新订阅沿用该会话的可读标签与平台名

### 修复

- 停用的订阅重新出现在 WebUI 订阅列表与 `/列表` 里，卡片标「已停用」并可随时点「启用」；此前列表只查启用中的记录，停用后既看不到也恢复不了
- 订阅间隔输入框清空即回落到继承上级默认（写入继承值），负数与超过 1440 分钟仍被拒绝

### 变更

- `webui_list_history` 返回可读的 `session_label`，推送历史卡片显示会话名而不是 32 位会话 ID
- 推送历史的状态徽章、看板副标题、`/列表` 与 `/状态` 的推送模式改为中文（人格化 / 直推 / 注入）
- 「下次抓取」与推送历史时间按浏览器本地时区显示，不再直接截 UTC 字符串
- 搜索框改为输入即筛（250 毫秒防抖），分页按钮在首页与末页置灰；看板首屏不再常驻音视频能力说明，该说明保留在插件配置页

### Added

- The WebUI subscription list offers a picker of known sessions, so bulk import no longer needs the host's internal session id by hand; importing into a known session reuses its readable label and platform name

### Fixed

- Disabled subscriptions show up again in the WebUI list and `/列表`, marked as disabled and re-enableable; the list previously queried only active rows, so pausing a subscription hid it for good
- Clearing the interval input now falls back to inheriting the upper level by writing the inherit value; negative values and anything above 1440 minutes are still rejected

### Changed

- `webui_list_history` returns a readable `session_label`, so history cards show the session name instead of a 32-character id
- History status badges, the dashboard subtitle, and the push mode in `/列表` and `/状态` are rendered in Chinese (persona / direct / injection)
- "Next fetch" and history timestamps are rendered in the browser's local time zone instead of a truncated UTC string
- The search box filters as you type with a 250 ms debounce, pager buttons are disabled on the first and last page, and the dashboard no longer pins the audio/video capability note at the top (it stays on the plugin config page)

## [0.7.0] - 2026-11-03

### 新增

- 注入模式带图：条目有新图时先压到 1 MiB 以内再注入上下文，注入消息带不超过 12 字符的消息 ID，麦麦的回复编排据此引用这幅图，发不发由它自己决定
- 注入文本在带图时附一条提示，写明可引用的 `msg_id` 与图片序号，麦麦据此取图；只带正文图与封面图，表格渲染与音视频链接不随注入进上下文

### 变更

- `media.image_max_mib` 仍是下载门槛，注入路径在其之上再按 1 MiB 压缩；压不下去的图跳过并计入日志里的 `skipped`

### Added

- The injection mode now carries images: pictures are compressed to at most 1 MiB before entering the context, and each injected message gets a message id of at most 12 characters so MaiBot can reference the picture in its own reply and decide whether to send it
- Injected text gains a hint naming the referenceable message id and image index when pictures are attached; only body and cover images are injected, table renders and audio/video links stay out of the context

### Changed

- `media.image_max_mib` remains the download threshold; the injection path compresses further to 1 MiB and skips pictures that cannot fit, counting them in the logged `skipped`

## [0.6.0] - 2026-11-03

### 变更

- 注入模式（取值 `silent`，界面标签由「静默」改为「注入」）：把条目写进麦麦的聊天上下文后固定请它主动思考一轮，是否开口由它自己决定；`[silent] proactive_trigger` 开关取消，配置版本升至 1.4.0
- WebUI 管理台的操作反馈改为右下角轻提示：改参数、启停、删除后直接刷新列表，不再在页面顶部插入会挤动版式的提示条；错误与导入结果提示保留，仍不占文档流

### Changed

- The injection mode (`silent`, relabelled from "静默" to "注入" in the UI) appends entries to the chat context and always asks MaiBot to think one round about whether to speak; the `[silent] proactive_trigger` switch is gone and the config version moves to 1.4.0
- WebUI action feedback moved to a bottom-right toast: parameter edits, enable/disable and deletes refresh the list in place instead of inserting a layout-shifting banner; errors and import summaries keep their toast

## [0.5.0] - 2026-11-03

### 修复

- 推送历史按条目各存一份文本，发送失败后按条目逐条补发；再次推送同一条目前先查成功记录，已送达的跳过。此前整批文本会随重试被重复发送多次
- 定时轮询重建条目时携带正文 HTML，正文图片与表格渲染恢复；此前只有 `/测试推送` 能看到图
- HTTP 抓取使用配置里的单源超时（此前传 `None` 等于禁用超时），单源挂起不再拖住整组调度；域名解析移出事件循环
- 关闭自动重定向并逐跳复核落点，内网地址不再能通过 302 绕过校验
- 一轮调度里某个源抛异常不再丢弃同轮其它源的投递结果
- 媒体张数上限为 0 时完全不带图且保留音视频链接；超出上限的成功下载计入 `skipped`；`display_media=2` 开始生效
- 退订后不再向旧会话补发失败记录；推送历史清理覆盖边界日，并清理没有任何订阅与推送记录的源行
- 批量导入按真实新增计数，且必须填写目标会话，避免建出不会推送的订阅
- 订阅间隔限制在 1–1440 分钟；处理链工具校验订阅属主，跨会话读写被拒绝
- 重复订阅同一地址时复用已有源行，不再产生重复记录

### 新增

- 支持 JSON Feed 1.1 解析
- 会话默认值参与生效配置解析，顺序为订阅 → 会话默认 → 用户默认 → 全局默认

### 变更

- 移除 `display_via`、`style` 与 `input_scope=both` 这三个从未生效的选项（数据库列保留，不影响既有数据）
- `display_*` 选项的「0＝继承」说明改为「0＝自动」
- 新增依赖 `socksio`（socks 代理支持），移除未使用的 `filetype`
- `/测试推送` 与轮询共用同一套媒体预算：单图 8 秒、整批 12 秒、最多 3 张并发

### 迁移提示

- 升级后新的失败记录重试时只发该条目本身；此前留在历史里的失败记录同样是单条目文本
- 宿主会在重载插件时按声明安装新增依赖；若 socks 代理不可用，插件记一条 error 并按直连处理

### Fixed

- Push history now stores one text per entry and retries resend a single entry at a time; an entry that already has a success record is skipped on re-push (previously the whole batch text was resent on every retry)
- The polling path rebuilds entries with their content HTML, so scheduled pushes carry inline images and rendered tables again
- HTTP fetches use the configured per-feed timeout (a literal `None` used to disable it), and DNS resolution moved off the event loop
- Redirects are followed manually with a per-hop target check, closing the internal-address bypass through 302
- A single failing feed no longer discards the delivery results of other feeds in the same tick
- With the media limit set to 0 no images are attached while audio/video links remain; downloads beyond the limit are counted as `skipped`; `display_media=2` takes effect
- Failed records are no longer resent to sessions whose subscription was removed; history purge covers the boundary day and orphan feed rows
- Bulk import counts genuinely new subscriptions and requires a target session
- Subscription interval is validated to 1–1440 minutes; handler tools verify subscription ownership
- Concurrent subscriptions to the same address reuse the existing feed row

### Added

- JSON Feed 1.1 parsing
- Session defaults participate in settings resolution: subscription → session → user → global

### Changed

- Dropped three options that never took effect: `display_via`, `style` and `input_scope=both` (their database columns remain)
- The `display_*` options now describe `0` as "auto" instead of "inherit"
- Added the `socksio` dependency for socks proxies and dropped the unused `filetype`
- Media budget shared by `/测试推送` and polling: 8s per image, 12s per batch, 3 concurrent downloads

### Migration

- Failed records kept from older versions are single-entry texts as well; new retries only send their own entry
- The host installs newly declared dependencies when the plugin is reloaded; when socks is unavailable the plugin logs an error and connects directly

## [0.4.0] - 2026-11-02

### 变更

- 订阅表的会话标签改为可读形式：`QQ群:123456789` / `QQ私聊:123456789`（按平台映射 QQ/微信/Telegram 等），不再直接显示内部 stream_id；订阅表新增 `session_label` 列，数据库迁移到 v2
- WebUI 页面整体修缮：订阅与历史页改用宿主原生 `maibot-select`（不再自绘下拉），空列表与空历史给专门提示，链接单元格可折行，标题与说明不再重复

### 迁移提示

升级后老订阅的会话标签仍是原 stream_id，之后新建的订阅会带上 `QQ群:…` 形式；不影响推送行为。

### Changed

- Subscription session labels now read as `QQ群:123456789` / `QQ私聊:123456789` (platform names mapped) instead of the opaque stream id; a `session_label` column was added and the database migrated to v2
- The WebUI page was polished: native `maibot-select` dropdowns replace the custom widget, empty lists get proper placeholders, long links wrap, and duplicated titles are gone

### Migration

Older subscriptions keep their previous label; subscriptions created from now on carry the readable form. Delivery is unaffected.

## [0.3.2] - 2026-11-01

### 新增

- 配置项 `[plugin] tools_enabled`（「向麦麦提供订阅工具」）：关闭后不再向宿主注册 15 个 LLM 工具，麦麦只能通过命令与 WebUI 管理订阅；`config_version` 升至 1.3.0

### 说明

宿主只在插件注册时收集组件，切换开关后需要到插件页点一次「重载」；重载前工具仍在工具表里，但会直接回绝并提示已关闭。

### Added

- New `[plugin] tools_enabled` switch: with it off the plugin no longer registers its 15 LLM tools, leaving chat commands and the WebUI page as the only interfaces; `config_version` is now 1.3.0

### Note

The host collects components at plugin registration, so toggling the switch takes full effect after reloading the plugin; until then the tools stay in the tool list and refuse calls with a notice.

## [0.3.1] - 2026-11-01

### 变更

- 命令 id 统一为 `rss_sub` / `rss_unsub` / `rss_list` / `rss_status` / `rss_help` / `rss_test`（原 `rss_sub_list` / `rss_sub_status` / `rss_test_push` 改名）
- 每个命令同时接受中文与英文写法：`/订阅`｜`/退订`｜`/列表`｜`/状态`｜`/帮助`｜`/测试推送`（或 `/sub_test`），`/rsshelp` 帮助文本同步
- `/测试推送` 的单命令授权路径随之变为 `omsociety.rsshub.rss_test`

### Changed

- Command ids normalised to `rss_sub` / `rss_unsub` / `rss_list` / `rss_status` / `rss_help` / `rss_test`
- Every command accepts both Chinese and English forms: `/订阅`, `/退订`, `/列表`, `/状态`, `/帮助`, `/测试推送` (or `/sub_test`)
- The per-command permission key for the test push is now `omsociety.rsshub.rss_test`

## [0.3.0] - 2026-11-01

### 变更

- 配置项按「人看得懂」重做：分区改名为抓取与轮询、网络与代理、人格化播报、静默注入、富媒体与附件；每个字段的标签带单位，说明写清后果，提示写明何时该调
- 字段重命名：`polling.interval_seconds` → `interval_minutes`、`push.persona_timeout_ms` → `persona_timeout_seconds`、`media.image_max_bytes` → `image_max_mib`、`media.file_max_bytes` → `file_max_mib`，时间统一分钟/秒、体积统一 MiB
- 删除从未生效的 `push.batch_merge`；`config_version` 升至 1.2.0
- 新增命令 `/测试推送 [编号]`：立刻把某条订阅的最新 3 条推到当前会话，验证源、处理链与推送效果；由宿主按 operator 权限校验（`[plugin] permission` 或 `[plugin.command_permissions]` 单命令放行），不写推送历史、不影响定时轮询

### 迁移提示

重命名后的旧键（`interval_seconds` / `persona_timeout_ms` / `image_max_bytes` / `file_max_bytes`）不再被读取，宿主配置页会显示新字段并填入与旧默认相同的值；旧键留在 `config.toml` 里不生效，可手动删除。如果你改过这些旧键的值，请在新字段上重填。

### Changed

- Configuration is reorganised for readability: sections renamed, every label now carries its unit, descriptions state the consequence, and hints say when to change the value
- Renamed fields: `polling.interval_seconds` → `interval_minutes`, `push.persona_timeout_ms` → `persona_timeout_seconds`, `media.image_max_bytes` → `image_max_mib`, `media.file_max_bytes` → `file_max_mib`
- Removed the never-read `push.batch_merge`; `config_version` raised to 1.2.0
- New command `/测试推送 [编号]` pushes the latest three entries of a subscription into the current chat to verify feed, handler chain, and delivery; gated by the host operator permission (`[plugin] permission`), writes no history, and leaves the schedule untouched

### Migration

Renamed keys are no longer read; the plugin config page shows the new fields with the same defaults. Old keys remain in `config.toml` without effect and can be deleted. If you customised those values, re-enter them on the new fields.

## [0.2.3] - 2026-11-01

### 修复

- 推送历史模型缺少 `created_at` / `updated_at` / `completed_at` 字段，WebUI 的概况与历史查询在库里有记录时抛 `'PushHistory' object has no attribute 'created_at'`；补齐字段与行映射，并让概况测试带一条历史记录

### Fixed

- The push history model lacked `created_at`, `updated_at`, and `completed_at`, so the WebUI overview and history queries raised `'PushHistory' object has no attribute 'created_at'` whenever the database had records; the fields and row mapping are now complete, and the overview test inserts a history record

## [0.2.2] - 2026-11-01

### 文档

- README 顶部说明宿主「设置 → 能力 → 文件功能」开关何时需要打开：工具推送音视频原件需要它，定时推送无论开关如何都附链接
- WebUI 管理页标题下加一行提示，插件配置里「发送音视频文件」的说明同步标注该前提

### Docs

- README now explains when the host "Settings → Capabilities → File capabilities" switch is required: tool pushes need it, scheduled pushes always fall back to links
- The WebUI page shows the same prerequisite under its title, and the media option description mentions it

## [0.2.1] - 2026-11-01

### 修复

- `rss_push_xml_entry` 调用不存在的 `SubscriptionService.resolve_for_session`，工具整体报错退出；改为按订阅、用户、全局三级解析生效配置
- 同条目去重把 guid 当作 `entry_link` 比较，重复推送拦不住；改为按 `entry_guid` 且只统计成功记录
- 宿主关闭 `[capabilities] files` 时，工具推送在日志说明原因并退回链接行

### Fixed

- `rss_push_xml_entry` called a non-existent `SubscriptionService.resolve_for_session` and failed outright; effective settings are now resolved from subscription, user, and global defaults
- Entry deduplication compared a guid against `entry_link`, so repeat pushes slipped through; it now matches `entry_guid` and only counts successful records
- When the host disables `[capabilities] files`, tool pushes log the reason and fall back to link lines

## [0.2.0] - 2026-11-01

### 变更

- 基线抬到 MaiBot 1.3.19 与 `maibot-plugin-sdk` 2.21.0：`_manifest.json` 的 `host_application.min_version` 与 `sdk.min_version` 同步上调，CI 改为单版本矩阵
- 音视频发送：`rss_push_xml_entry` 工具推送时经宿主文件服务发送原文件（`files.upload` + `files.send`）；后台定时推送没有文件凭证，仍在正文末尾附链接行
- 配置 `config_version` 升至 1.1.0：`[media]` 新增 `attach_files`、`max_files`、`file_max_bytes`

### Changed

- Baseline raised to MaiBot 1.3.19 and `maibot-plugin-sdk` 2.21.0: manifest minimums follow, and CI runs a single-version matrix
- Audio and video: `rss_push_xml_entry` now delivers original files through the host file service (`files.upload` + `files.send`); scheduled pushes have no file scope and keep appending links
- `config_version` is now 1.1.0: `[media]` gains `attach_files`, `max_files`, and `file_max_bytes`

## [0.1.0] - 2026-11-01

### 新增

- 订阅核心：RSS/Atom/JSON Feed 抓取、条件请求（ETag / Last-Modified）、条目哈希与推送历史两层去重、失败重试
- 15 个 LLM 工具（订阅管理、选项调整、推送历史、handler 链与 XML 条目直推）与 5 个聊天命令（/sub /unsub /sub_list /sub_status /rsshelp）
- 三种推送模式：人格化（调用 mai-saka 生成，失败回退直推）、直推、静默（写入上下文并可选触发主动思考）
- 富媒体：正文图与封面图下载、图文混排、多图合并转发、表格渲染为图片；音频与视频降级为链接卡
- handler 链：ai_filter 内容过滤与 ai_transform 文本改写，支持订阅级与用户级覆盖
- WebUI 管理台：订阅列表与筛选、推送历史、参数调整、启停与删除、立即抓取、批量导入
- 配置分 8 个分区（插件、轮询、网络、推送、人格化、静默、媒体、RSSHub），保存后热重载

### Added

- Subscription core: RSS/Atom/JSON Feed fetching, conditional requests (ETag / Last-Modified), two-layer deduplication (entry hashes plus push history idempotency), and failure retries
- 15 LLM tools (subscription management, option tuning, push history, handler chain, direct XML entry push) and 5 chat commands (/sub /unsub /sub_list /sub_status /rsshelp)
- Three push modes: persona (generated in character, falling back to direct text), direct text, and silent (context injection with optional proactive thought)
- Rich media: body and cover image download, mixed text and image delivery, multi-image forward, table rendering to image; audio and video degrade to link cards
- Handler chain: ai_filter for content filtering and ai_transform for rewriting, overridable per subscription and per user
- WebUI management page: subscription list with filtering, push history, option tuning, enable/disable and delete, poll now, batch import
- Configuration across 8 sections (plugin, polling, network, push, persona, silent, media, RSSHub) with hot reload on save

[0.4.0]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.4.0
[0.3.2]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.3.2
[0.3.1]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.3.1
[0.3.0]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.3.0
[0.2.3]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.2.3
[0.2.2]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.2.2
[0.2.1]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.2.1
[0.2.0]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.2.0
[0.1.0]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.1.0
