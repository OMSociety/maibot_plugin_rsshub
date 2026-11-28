# Changelog

本项目的更改记录在此文件。

All notable changes to this project are documented in this file.

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.1] - 2026-11-28

### Changed

- `push.default_mode` 默认值改为 `silent`：新订阅与 `inherit` 订阅把条目注入聊天上下文，由麦麦决定是否开口。
- `push.default_mode` now defaults to `silent`: new and inheriting subscriptions inject entries into the chat context and let the bot decide whether to speak.

### Fixed

- `ETag` 与 `Last-Modified` 按响应头原样取值并落库，下一轮请求携带 `If-None-Match` 与 `If-Modified-Since`。
- `ETag` and `Last-Modified` are read and stored from the response, and the next poll sends `If-None-Match` / `If-Modified-Since`.

## [1.1.0] - 2026-11-24

### Changed

- LLM 工具更名：`rss_list_subscriptions` → `rss_list_feeds`，`rss_set_subscription_option` → `rss_set_feed_option`，`rss_set_subscription_handlers` → `rss_set_feed_handlers`。
- LLM tools renamed: `rss_list_subscriptions` → `rss_list_feeds`, `rss_set_subscription_option` → `rss_set_feed_option`, `rss_set_subscription_handlers` → `rss_set_feed_handlers`.

### Fixed

- `rss_list_push_history` 的 `limit` 参数传入字符串时不再抛 `TypeError`。
- `rss_list_push_history` no longer raises `TypeError` when `limit` is passed as a string.
