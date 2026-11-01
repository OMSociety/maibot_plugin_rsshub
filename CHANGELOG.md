# Changelog

本项目的更改记录在此文件。

All notable changes to this project are documented in this file.

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.2.0
[0.1.0]: https://github.com/OMSociety/maibot_plugin_rsshub/releases/tag/v0.1.0
