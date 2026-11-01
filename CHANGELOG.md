# Changelog

本项目的更改记录在此文件。

All notable changes to this project are documented in this file.

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-11-01

### 变更

- 配置项按「人看得懂」重做：分区改名为抓取与轮询、网络与代理、人格化播报、静默注入、富媒体与附件；每个字段的标签带单位，说明写清后果，提示写明何时该调
- 字段重命名：`polling.interval_seconds` → `interval_minutes`、`push.persona_timeout_ms` → `persona_timeout_seconds`、`media.image_max_bytes` → `image_max_mib`、`media.file_max_bytes` → `file_max_mib`，时间统一分钟/秒、体积统一 MiB
- 删除从未生效的 `push.batch_merge`；`config_version` 升至 1.2.0
- 新增命令 `/测试推送 [编号]`：立刻把某条订阅的最新 3 条推到当前会话，验证源、处理链与推送效果；由宿主按 operator 权限校验（`[plugin] permission`），不写推送历史、不影响定时轮询

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

[0.3.0]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.3.0
[0.2.3]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.2.3
[0.2.2]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.2.2
[0.2.1]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.2.1
[0.2.0]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.2.0
[0.1.0]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.1.0
