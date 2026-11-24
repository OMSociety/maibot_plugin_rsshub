# Changelog

本项目的更改记录在此文件。

All notable changes to this project are documented in this file.

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-11-24

### Changed

- LLM 工具更名：`rss_list_subscriptions` → `rss_list_feeds`，`rss_set_subscription_option` → `rss_set_feed_option`，`rss_set_subscription_handlers` → `rss_set_feed_handlers`。
- LLM tools renamed: `rss_list_subscriptions` → `rss_list_feeds`, `rss_set_subscription_option` → `rss_set_feed_option`, `rss_set_subscription_handlers` → `rss_set_feed_handlers`.

### Fixed

- `rss_list_push_history` 的 `limit` 参数传入字符串时不再抛 `TypeError`。
- `rss_list_push_history` no longer raises `TypeError` when `limit` is passed as a string.
