# AGENTS.md — maibot_plugin_rsshub Agent 宪法

适用范围：本仓库全部目录。子目录若出现 AGENTS.md，以更接近改动点的那份为准。
最后更新：2026-11-01

## 项目概览

- 一句话定位：MaiBot（麦麦）的 RSS/Atom 订阅插件，负责定时抓取、去重推送，并把订阅读写暴露成 LLM 工具、聊天命令与 WebUI 页面。
- 技术栈：Python ≥3.11（CI 与宿主都用 3.12，验收以 3.12 为准；3.11 未被 CI 覆盖，仓库也没有声明 requires-python）；`maibot-plugin-sdk` 2.21.0–2.21.99（宿主 MaiBot 1.3.19 起）；SQLite（WAL）；feedparser / beautifulsoup4 / lxml / filetype；pytest + ruff；GitHub Actions 跑声明下限的 SDK。
- 文档索引：
  - 功能、安装、配置、命令与工具表：[README.md](./README.md)
  - 变更记录：[CHANGELOG.md](./CHANGELOG.md)
  - 完整配置示例（含注释）：[config.example.toml](./config.example.toml)
  - 宿主契约（manifest 与能力）：[_manifest.json](./_manifest.json)

## 常用命令

| 目的 | 命令 |
|---|---|
| 安装依赖 | `pip install -r requirements.txt -r requirements-dev.txt "maibot-plugin-sdk==2.21.0"` |
| 跑全部测试 | `python -m pytest -q` |
| 跑单个测试文件 | `python -m pytest tests/test_dispatcher.py -q` |
| Lint | `ruff check .` |
| 格式检查 | `ruff format --check .` |
| 校验 WebUI 声明 | `python tools/check_webui.py .`（核对 `webui.json` 的 `api` 与 `web_api.py` 的 `@API` 名称、参数一致，页面入口存在且不超过 2 MiB，能力名合法） |
| 校验本文件 | `python tools/check_agents_md.py --root .` |

验证更高版本的 SDK 时，把 `maibot-plugin-sdk==2.21.0` 换成目标版本重装即可；也可以用 `MAIBOT_SDK_PATH` 指向解压后的 SDK 目录，测试夹具会优先用它。

```bash
python -m pytest -q
```

## 架构边界

```text
plugin.py（唯一入口：配置模型 + 生命周期 + 组件声明）
  └─> rsshub/interfaces/web_api.py     WebUI 的 8 个 @API
  └─> rsshub/application               services / tools / commands
        └─> rsshub/domain              实体与三级配置继承
        └─> rsshub/infrastructure      persistence / fetcher / media / rendering / schedule
```

- 允许：`plugin.py` 与 `interfaces` 组装 `application` 的服务；`application` 调 `domain` 与 `infrastructure`。
- 禁止：`infrastructure` 反向依赖 `application` 或 `interfaces`。原因：抓取、解析、存储在测试里要能脱离宿主单独跑。
- 禁止：`domain` 导入 `maibot_sdk` 或持有宿主上下文。原因：领域层要能在没有宿主进程的环境里做纯逻辑测试。
- 禁止：在 `rsshub/` 内写 `from src.*`。原因：`src` 是宿主自己的源码树，插件校验器会判为导入宿主内部模块；本包名是 `rsshub`。
- 禁止：业务模块直接调用 `ctx.send` / `ctx.maisaka`。原因：发送必须经过 `Dispatcher` 统一处理降级与推送历史，旁路会绕开幂等。
- 工具与命令由 Runner 遍历插件实例的 `dir()` 收集，带 `@Tool` / `@Command` / `@API` 装饰器的混入方法会自动注册，不需要清单文件；注册期任何一个组件声明抛错，整批注册失败、插件变 `FAILED`。
- 若新工具需要新的应用服务：在 `plugin.py` 的 `_boot` 里构造并挂到 `self`，同时在 `on_unload` 释放、在 `on_config_update` 重建。

## 关键目录

| 我要… | 改这里 |
|---|---|
| 加一个 LLM 工具 | 顺序：① 在 `rsshub/application/tools/rss_tools.py` 仿 `rss_subscribe` 写方法（混入类由 Runner 自动收集，不需要在 `plugin.py` 里登记）→ ② 若用到新的 `ctx.*` 能力，同一次改动补 `_manifest.json` 的 `capabilities` → ③ 补测试 → ④ 同步 README 的工具表与 CHANGELOG 里的数量 → ⑤ 跑验收五项 |
| 加一个聊天命令 | `rsshub/application/commands/rss_commands.py`（`@Command` 的 `pattern` 由宿主用 `re.search` 匹配，参数从 `matched_groups` 取；处理函数必须自己 `ctx.send.text`，宿主不代发） |
| 加或改 WebUI 接口 | `rsshub/interfaces/web_api.py` 加 `@API`，再在 `webui.json` 的 `queries`/`actions` 里声明绑定 |
| 改抓取、解析、去重 | `rsshub/infrastructure/fetcher/`（`client` / `parser` / `dedup` / `url_guard`） |
| 改表结构 | `rsshub/infrastructure/persistence/database.py` 的 `SCHEMA_VERSION` 与迁移；同步 `repositories.py`、`models.py` |
| 改发送形态 | `rsshub/application/services/dispatcher.py`（persona / direct / silent 三路） |
| 加测试 | `tests/test_*.py`；宿主与 SDK 的桩在 `tests/support.py` |

## 数据流

`/sub` 或 `rss_subscribe` → `SubscriptionService`（`normalize_feed_url` + SSRF 校验）→ `HttpClient` 条件抓取 → `parse_feed` → `FeedPoller`（哈希去重、首轮只建基线、投递全成功才推进 ETag）→ `ContentHandlerChain` → `Dispatcher` → `push_history` 落库；`Scheduler` 自起 asyncio 循环按 30 秒 tick 取到期订阅。

## 修改契约

- 改配置结构或默认值：改 `plugin.py` 的 config_model；`config_version` 是字符串 semver（当前 `1.1.0`），增删字段或改默认值时递增次要版本、改变字段含义时递增主版本。`config.toml` 由宿主生成，不要提交。
- 改能力调用：新增任何 `ctx.*` 能力调用，必须同步 `_manifest.json` 的 `capabilities`，否则运行期报 `E_CAPABILITY_DENIED`。
- 改 `webui.json` 或绑定：宿主在插件注册时校验声明，无效会让整个插件注册失败；改完必须重载插件并确认日志没有「WebUI 声明无效」。
- 改版本号：`_manifest.json` 的 `version`、git tag `vX.Y.Z`、`CHANGELOG.md` 条目三处必须一致；bump 前先和仓库所有者确认（本仓库由 OMSociety 单人维护）。新增功能 minor、修复 patch、破坏性变更 major，`0.x` 阶段沿用同一规则。
- `plugin.config_version` 与 `_manifest.json` 的 `version` 相互独立：前者只跟配置结构走，发版不必动它。
- 改依赖：Python 包同时写进 `requirements.txt` 与 `_manifest.json` 的 `dependencies`。
- 用 SDK 新 API：本插件声明下限 2.21.0，2.22.0 之后才有的能力必须在调用处加版本判断与降级，并保留 `REMOVAL:` 注释说明何时删除。
- 音视频文件只能经工具调用发送：宿主只为模型工具调用签发文件凭证（`ctx.files.*`），后台调度器推送没有凭证，音视频一律附链接行。不要为了「统一体验」把链接行改成 `ctx.files.send`，后台会抛 `PermissionError`。

## 禁止操作

- 禁止提交 `config.toml`、`*.db`、`data/`。原因：含本机代理地址与真实订阅数据，已在 `.gitignore` 排除。
- 禁止改写已发布的 tag 或 Release。原因：插件市场按 tag 索引版本。要修只能发新版本。
- 禁止绕过 `rsshub/infrastructure/fetcher/url_guard.py` 直接请求用户给的 URL。原因：它承担 SSRF 拦截（环回、私网、链路本地地址）。
- 禁止删除 `push_history` 成功的幂等记录来「让测试通过」。原因：去重与重试依赖它，删记录会导致重复推送。
- 禁止手改 `tools/check_agents_md.py`。原因：它是从 agent-onboarding 技能原样拷贝的校验脚本，改样式会与上游脱节；升级时整份替换即可（已在 `ruff.toml` 的 `exclude` 里排除）。

## 验收标准

改动完成 = 下列全部通过（CI 在 SDK 2.21.0 上跑一遍）：

1. `python -m pytest -q`
2. `ruff check .`
3. `ruff format --check .`
4. `python tools/check_webui.py .`
5. `python tools/check_agents_md.py --root .`

CI 跑声明下限 SDK 2.21.0；补丁版之间不逐个跑。

## 提交与协作

- 分支：直接在 `main` 上提交，发布用 tag。
- 发版：改 `_manifest.json` 的 `version` 与 `CHANGELOG.md` → `git add -A && git commit -m "release: vX.Y.Z" && git push origin main` → `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z` → `gh release create vX.Y.Z --notes-file <正文文件>`（正文即 CHANGELOG 对应条目，中英双语；正文文件放在仓库外）。
- CI 在 `.github/workflows/ci.yml`，push 到 `main`、推 tag、开 PR 时触发；`tag matches manifest` 任务只在 tag 推送时运行，核对 tag 与 manifest 版本。
- Commit：沿用历史前缀 `feat:` / `fix:` / `docs:` / `chore:`，正文写清为什么改。

## 已知风险区

| 路径 | 风险 | 改动前后置动作 |
|---|---|---|
| `webui.json` | 宿主用严格模型校验，绑定参数多一个字段就会被拒 | 改完调用一次页面动作，确认没有 422 |
| `rsshub/infrastructure/fetcher/dedup.py` | 首轮 `entry_hashes` 为空时只建基线不推送 | 联调要造「新条目」时改哈希或清条件头，不要删库重建 |
| `rsshub/application/services/polling.py` | 未全部投递成功会回滚 ETag，靠推送历史去重 | 改投递逻辑时保留该回滚，否则会漏推 |
| `_manifest.json` | 能力声明与代码不一致时运行期才报错 | 加 `ctx.*` 调用时同一次改动里补声明 |
| `rsshub/application/services/dispatcher.py` 与 `tools/rss_tools.py` | 音视频只有工具调用能发文件，后台推送附链接；误用 `ctx.files` 会在调度器里抛 `PermissionError` | 改音视频呈现时先确认调用来源是否有文件凭证 |
| `config.toml` | 宿主对未知键是静默忽略，拼错字段名不会报错，只会一直用默认值 | 改配置后到 WebUI 的插件配置页逐项核对，或看宿主 `logs/app_*.log.jsonl` 里 `plugin.omsociety.rsshub` 的日志 |
| 部署目录 | 宿主插件目录名是 `omsociety_rsshub`，包名是 `rsshub` | 同步时拷目录内容，不要为了对齐去改包名 |

## 出错怎么办

| 症状（报错里的可检索片段） | 处理 |
|---|---|
| `E_CAPABILITY_DENIED` / `未获授权能力` | `_manifest.json` 的 `capabilities` 缺项，补上后重载插件 |
| `WebUI 声明无效` | `webui.json` 字段或绑定不合 schema，按日志里的字段名修 |
| 插件整体 `FAILED`、组件一个都没注册 | 组件声明期抛异常，查日志里第一条 traceback |
| `attempted relative import with no known parent package` | 以脚本方式直接跑了包内文件；测试走 pytest，包从仓库父目录导入 |
| 页面上动作返回 422 / `extra_forbidden` | 绑定调用体必须是 `{"args": {...}}`，不能平铺字段 |

## 维护

本文件与代码同一次提交更新。改动以下内容时必须同步本文件：验收命令、分层边界、能力声明方式、工具/命令数量（README 与 CHANGELOG 同改）、发版流程、风险区。
发现内容与代码不符时，先改本文件，再继续改代码。
