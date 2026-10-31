#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AGENTS.md / CLAUDE.md 一致性检查 —— 仅标准库，只读，不联网，不写盘。

检查项
  1. 模板占位符残留（[项目名]、<仓库名>、<命令>、TBD ...；行内代码示例与链接标签不算）
  2. Markdown 相对链接的目标是否存在（行内链接与 [label]: ./path 引用式定义都查）
  3. `npm run <name>` / `make <target>` 是否真有对应 script / 目标
     （真相源从文档所在目录向上找最近的 package.json / Makefile，monorepo 模块不误报）
  4. ``` 围栏 shell 块（bash/sh/console/powershell/cmd 等）里的命令能否在仓库配置或
     当前 PATH 中核实——仅覆盖 ``` 围栏 shell 块，表格与行内命令需人工核
  5. 行数预算（默认 >200 提示，>500 警告）
  6. "最后更新"日期：没填 error、非 ISO 格式 info、陈旧 warn（默认 180 天）
  7. 裸禁令：只说"禁止…"，没有原因、命令或链接（启发式，仅扫列表行，仅提示）
  8. 同目录 AGENTS.md / CLAUDE.md 内容漂移（两文件共存时比对：不一致 warn，
     完全相同、仅换行符不同、指针形态均为 info）

用法
  python check_agents_md.py [--root <目录>] [--strict] [--json] [--no-path]
                            [--budget 200] [--max-lines 500] [--stale-days 180]
  --no-path：关闭 PATH 兜底，CI 用——保证本地与 CI 结果一致。

退出码
  0 = 无 error；1 = 有 error（--strict 时 warning 也导致失败）；2 = 用法或路径错误。

依赖
  无第三方依赖；需要 Python 3.8+（脚本启动时自检）。
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote

if sys.version_info < (3, 8):
    sys.stderr.write(
        "check_agents_md.py 需要 Python 3.8 及以上（当前 %s，路径 %s）\n"
        % (sys.version.split()[0], sys.executable)
    )
    raise SystemExit(2)

# 输出统一按 UTF-8 写，避免 Windows 上按本地代码页写出、被调用方按 UTF-8 解读成乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):   # 非交互场景 / 已被重定向包装
        pass

DOC_NAMES = ("AGENTS.md", "CLAUDE.md")
MAX_DEPTH = 8
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor", "third_party",
    ".venv", "venv", "env", "virtualenv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", "dist", "build", "out", "target", "coverage", "htmlcov",
    ".next", ".nuxt", ".cache", ".gradle", ".idea", ".vscode", "site-packages",
}

SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console", "shell-session",
               "ps1", "powershell", "cmd", "bat", "dos", "fish"}

# 仓库里"命令真相源"的候选文件
CORPUS_FILES = [
    "package.json", "Makefile", "makefile", "GNUmakefile", "justfile", "Justfile",
    "Taskfile.yml", "Taskfile.yaml", "pyproject.toml", "setup.py", "setup.cfg",
    "tox.ini", "noxfile.py", "Cargo.toml", "go.mod", "composer.json", "Gemfile",
    "Rakefile", "docker-compose.yml", "docker-compose.yaml", "compose.yml",
    "compose.yaml", "Dockerfile", ".gitlab-ci.yml", ".pre-commit-config.yaml",
    "CONTRIBUTING.md", "BUILD.bazel", "WORKSPACE",
]

PACKAGE_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
PM_BUILTINS = {
    "install", "i", "ci", "add", "remove", "rm", "uninstall", "un", "update", "up",
    "upgrade", "audit", "init", "publish", "link", "exec", "dlx", "why", "outdated",
    "config", "doctor", "help", "version", "view", "pack", "prune", "rebuild", "fund",
    "ls", "list", "explain", "dedupe", "dist-tag", "owner", "ping", "search", "login",
    "logout", "token", "unpublish", "whoami", "repo", "bugs", "docs", "root", "prefix",
    "bin", "cache", "completion", "edit", "explore", "hook", "shrinkwrap", "star",
    "stars", "team", "unstar", "create", "approve", "patch", "patch-commit", "deploy",
    "env", "import", "licenses", "pkg", "store", "prune", "setup", "self-update",
}

# 命令行里的 shell 关键字与无需核实的家常命令
SKIP_PROGRAMS = {
    "cd", "export", "set", "source", ".", "if", "for", "while", "until", "then", "fi",
    "do", "done", "case", "esac", "function", "return", "local", "echo", "printf",
    "exit", "true", "false", "read", "test", "sudo", "env", "time", "xargs",
    "cat", "ls", "pwd", "mkdir", "rmdir", "cp", "mv", "touch", "head", "tail", "wc",
    "grep", "sed", "awk", "sort", "uniq", "which", "sleep", "chmod",
    # cmd/PowerShell 的内建与别名：dir/copy/del 等在任何 Windows 机器上都跑得通，
    # 不该按"PATH 上找不到"报（AM-04）
    "dir", "copy", "del", "ren", "type", "md", "rd", "move", "cls",
}

# 运载工具（runner）：文档里写 `npx eslint` 时，该被核实的是 eslint 而不是 npx；
# runner 自身只在整条候选链里没有实质目标时才作为报错对象（AM-09）
RUNNER_PROGRAMS = {
    "python", "python3", "py", "uv", "poetry", "pdm", "hatch",
    "npx", "pnpx", "bunx", "dlx", "npm", "pnpm", "yarn", "bun",
}

# PowerShell cmdlet 的 Verb-Noun 形态（Get-ChildItem / Remove-Item ...）是解释器
# 内建能力，用 PATH 核实必然误报，直接放行（AM-04）
PS_CMDLET_RE = re.compile(r"^[A-Z][a-z]+-[A-Z][A-Za-z]+$")

# 第三元控制在哪条线上匹配（AM-05 裁决口径是"上下文排除"，不是一刀切）：
#   "code" = 先剥行内代码再查——`[待补充]` 这类是示例形态，不是要填的空；
#   "raw"  = 只剥链接、保留行内代码——模板的主导占位风格正是带反引号的 `<命令>`，
#            若行内代码整体剥掉，AM-01 要抓的形态会被重新藏回去。
PLACEHOLDERS = [
    (re.compile(r"\[[^\]\n]*(?:项目名|仓库名|模块名|一句话|描述|名称|待填|待补充|必填|占位|TODO|TBD|XXX)[^\]\n]*\]"),
     "方括号占位符", "code"),
    # {0,40} 允许关键词紧跟 <，否则自家模板最高频的 <仓库名>/<命令> 一处都抓不到（AM-01）；
    # 命令/动作/路径/为什么/片段是模板里的高频占位词
    (re.compile(r"<[^<>\n]{0,40}(?:仓库名|项目名|模块名|命令|动作|路径|为什么|片段|名称|描述|你的|占位|必填|TODO|TBD|placeholder|xxx)[^<>\n]*>",
                re.IGNORECASE),
     "尖括号占位符", "raw"),
    (re.compile(r"\bTBD\b"), "TBD", "code"),
    (re.compile(r"\bFIXME\b"), "FIXME", "code"),
]

# 链接目标允许一层成对括号（./guide_(v2).md 不会被第一个 ) 截断，AM-14）；title 单双引号都认（AM-22）
LINK_RE = re.compile(r"\[[^\]\n]*\]\(\s*([^)\s](?:[^()\s]|\([^()]*\))*)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
# 引用式链接定义行：[label]: ./target（AM-13）
REF_LINK_DEF_RE = re.compile(r"^\s*\[([^\]\n]+)\]:\s*(\S+)\s*$")
# 行内代码：占位符检查前要先剥掉的示例代码（AM-05）
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
# 宽松日期样式（2026/10/05、2026年10月 之类）：不是 ISO，脚本核不了新旧（AM-06）
LOOSE_DATE_RE = re.compile(r"\d{4}[-/.年]\d{1,2}")
# 围栏行（开启形态）：``` 或 ~~~，后面只允许 info 串（AM-10 支持波浪围栏）。
# 闭合判定在扫描器里按 CommonMark 规则做：同字符、长度不小于开启、尾部只余空白。
# 行尾 \r 在扫描前统一剥掉（CRLF 文档的围栏行才认得出来）。
FENCE_LINE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*(\S.*?)?$")
DATE_RE = re.compile(
    r"(?i)(?:last\s+updated|last\s+modified|最后更新|更新时间|更新日期)\s*[:：]?\s*"
    r"(\d{4})-(\d{1,2})-(\d{1,2})")
DATE_PREFIX_RE = re.compile(r"(?i)last\s+updated|last\s+modified|最后更新|更新时间|更新日期")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")
# 裸禁令"已给原因"启发式拆两层（AM-23）：中文提示词仍是子串匹配；英文 why 要
# 词边界、且不能是文件名/标识符的一段——why.md 里的 why 不算给了理由
REASON_HINT_CN = re.compile(r"除非|以免|否则|因为|原因|历史|事故|避免|防止|如遇|若有|理由")
REASON_HINT_EN = re.compile(r"(?<![\w.\-])why(?![\w.\-])", re.IGNORECASE)
PROHIBIT_HINT = re.compile(r"禁止|不得|绝不|切勿")

LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}


def read_text(path):
    try:
        return path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""


def rel_to(path, root):
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def link_target_exists(path, target):
    """相对链接目标是否存在；带 scheme、纯锚点、绝对路径等不在检查口径内的视作通过。"""
    if target.startswith("#") or target.startswith("/"):
        return True
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):   # 带 scheme：http/mailto/file...
        return True
    clean = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not clean:
        return True
    return (path.parent / clean).exists()


def discover(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        try:
            depth = len(d.relative_to(root).parts)
        except ValueError:
            depth = 0
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        else:
            dirnames[:] = sorted(
                x for x in dirnames if x not in SKIP_DIRS and not x.startswith("."))
        for name in sorted(filenames):
            if name in DOC_NAMES:
                found.append(d / name)
    return found


def build_corpus(root):
    chunks = []
    for name in CORPUS_FILES:
        p = root / name
        if p.is_file():
            chunks.append(read_text(p))
    for d in (root / ".github" / "workflows", root / ".circleci"):
        if d.is_dir():
            for p in sorted(d.rglob("*")):
                if p.is_file() and p.suffix.lower() in (".yml", ".yaml"):
                    chunks.append(read_text(p))
    return "\n".join(chunks)


def word_in_corpus(token, corpus):
    if not corpus or not token:
        return False
    return re.search(r"(?<![\w./-])" + re.escape(token) + r"(?![\w./-])", corpus) is not None


def package_script_name(prog, args):
    if not args:
        return None
    head = args[0]
    if head in ("run", "run-script"):
        # 脚本名可能带引号（npm run "test"）：先剥再比对，别把引号当名字的一部分（AM-15）
        return args[1].strip("\"'") if len(args) > 1 else None
    if head in ("test", "t", "start", "stop", "restart"):
        return head
    if head.startswith("-") or head in PM_BUILTINS:
        return None
    return head.strip("\"'")


def nearest_file(start_dir, root, names):
    """从文档所在目录向上（含本目录，到 root 为止）找最近的真相源文件。

    monorepo 的模块级 AGENTS.md 里写的命令，真相源是离它最近的那份
    package.json / Makefile，不是仓库根那份（AM-07）。一路到 root 都没有
    就返回 None，由调用方给 info 提示，而不是静默跳过（AM-15）。
    """
    d = start_dir
    while True:
        for name in names:
            p = d / name
            if p.is_file():
                return p
        if d == root:
            return None
        parent = d.parent
        if parent == d:   # 防御：start_dir 不在 root 之下时别无限上溯
            return None
        d = parent


def makefile_has_target(makefile, target):
    text = read_text(makefile)
    return re.search(r"(?m)^" + re.escape(target) + r"\s*:(?!=)", text) is not None


def parse_invocation(raw):
    """把一行 shell 拆成 (程序, 参数列表)；不是可判定的命令时返回 None。"""
    s = raw.strip()
    if not s:
        return None
    if s.startswith("#") or s.startswith("//") or s.startswith("::"):
        return None
    if s.lower().startswith("rem "):
        return None
    # REPL 提示符与 npm 等工具的输出行（"> pkg@1.0.0 test"）不是人敲的命令（AM-03）；
    # 因此 "> " 不进下面的提示符剥离表，避免输出行被剥掉前缀后误当命令解析
    if s.startswith(">>>") or s.startswith("...") or s.startswith("> "):
        return None
    # 常见纯输出行按开头识别（启发式：宁可少核一条，不能把测试输出当命令报 warn）
    low = s.lower()
    if low.startswith("all tests passed") or re.match(r"(?:ok|pass|fail|traceback)\b", low):
        return None
    for prefix in ("$ ", "PS> ", "PS "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
            break
    # 围栏里直接抄编号/项目符号列表行（"1. npm run x"）的情况：剥一次列表前缀（AM-21）
    s = re.sub(r"^\s*(?:\d+[.)]|[*+])\s+", "", s, count=1)
    if "…" in s or "..." in s:
        return None
    if re.search(r"<[^>]*>", s):          # 含占位参数，无法判定
        return None
    if not s or s.startswith("|") or s.startswith("&&") or s.startswith("||"):
        return None
    tokens = s.split()
    first = tokens[0].strip("\"'")
    if not first or first.startswith("-"):
        return None
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", first):   # 环境变量赋值开头
        return None
    rest = [t for t in tokens[1:]]
    # 只取到第一个 shell 连接符之前（后半段各算一条）
    head = []
    for t in rest:
        if t in ("|", "||", "&&", ";", ">", ">>", "2>", "2>&1"):
            break
        head.append(t)
    return first, head


def add(findings, level, file, line, code, message):
    findings.append({"level": level, "file": file, "line": line,
                     "code": code, "message": message})


def iter_fenced_blocks(numbered):
    """逐行状态机扫出成对围栏代码块：产出 (info 串, [(行号, 行文本), ...])。

    替换原先的整体正则 FENCE_RE（AM-10）：
    - 波浪围栏 ~~~ 原先完全不认；
    - 嵌套围栏（用更长围栏包示例的写法）原先会被非贪婪匹配错切，整段逃检；
    - 闭合按 CommonMark 规则：同字符、长度不小于开启、尾部只余空白——
      带 info 串的 ```bash 是开启不是闭合。
    未闭合到 EOF 不产出，避免把下一个块的标记行错当命令报。
    """
    open_marker = None
    open_info = ""
    body = []
    for no, line in numbered:
        m = FENCE_LINE_RE.match(line)
        if open_marker is None:
            if m:
                open_marker = m.group(1)
                open_info = (m.group(2) or "").strip()
                body = []
            continue
        if m and m.group(1)[0] == open_marker[0] and len(m.group(1)) >= len(open_marker) \
                and not (m.group(2) or "").strip():
            yield open_info, body
            open_marker = None
            body = []
        else:
            body.append((no, line))
    # 走到 EOF 还开着：不产出（防错配）


def check_commands_for_doc(path, rel, text, root, corpus, findings, no_path=False):
    numbered = [(i, ln.rstrip("\r")) for i, ln in enumerate(text.split("\n"), 1)]

    # 真相源从文档所在目录向上找最近的一份（AM-07）；整个文档只解析一次，
    # 坏 JSON 在文件级 warn 一次并跳过比对，不让每条 npm run 连坐报 error（AM-08）
    pkg = nearest_file(path.parent, root, ("package.json",))
    pkg_scripts = None
    if pkg is not None:
        try:
            pkg_scripts = (json.loads(read_text(pkg) or "{}") or {}).get("scripts") or {}
        except ValueError:
            add(findings, "warn", rel, 0, "package-json",
                "package.json 解析失败，跳过 npm script 比对：%s" % rel_to(pkg, root))
    makefile = nearest_file(path.parent, root, ("Makefile", "makefile", "GNUmakefile"))

    def check_line(line_no, raw):
        parsed = parse_invocation(raw)
        if not parsed:
            return
        prog, args = parsed

        # PowerShell cmdlet（Get-ChildItem 等 Verb-Noun 形态）是解释器内建，直接放行（AM-04）
        if PS_CMDLET_RE.match(prog):
            return

        if prog in PACKAGE_MANAGERS:
            script = package_script_name(prog, args)
            if script:
                if pkg is None:
                    add(findings, "info", rel, line_no, "script-source",
                        "无法核对 npm script：向上未找到 package.json（%s）" % raw.strip()[:70])
                elif pkg_scripts is not None and script not in pkg_scripts:
                    add(findings, "error", rel, line_no, "script",
                        "package.json 的 scripts 里没有「%s」（出现在：%s）"
                        % (script, raw.strip()[:70]))
            return

        if prog in ("make", "gmake") and args:
            target = args[0]
            if not target.startswith("-"):
                if makefile is None:
                    add(findings, "info", rel, line_no, "make-source",
                        "无法核对 make 目标：向上未找到 Makefile（%s）" % raw.strip()[:70])
                elif not makefile_has_target(makefile, target):
                    add(findings, "error", rel, line_no, "make-target",
                        "Makefile 里没有目标「%s」（出现在：%s）" % (target, raw.strip()[:70]))
            return

        candidates = []
        if prog in ("python", "python3", "py") and args[:1] == ["-m"] and len(args) > 1:
            candidates.append((args[1], "模块"))
        elif prog in ("uv", "poetry", "pdm", "hatch") and "run" in args:
            idx = args.index("run")
            if len(args) > idx + 1:
                candidates.append((args[idx + 1], "命令"))
        elif prog in ("npx", "pnpx", "bunx", "dlx") and args:
            candidates.append((args[0], "命令"))
        candidates.append((prog, "命令"))

        # 报第一个无法核实的实质目标；runner 只是运载工具，不作为报错对象，
        # 仅当候选链里没有实质目标可报时才拿 runner 兜底（AM-09）
        unverified = None
        for token, kind in candidates:
            if not token or token.startswith("-") or "/" in token or "\\" in token:
                return
            if token in SKIP_PROGRAMS or word_in_corpus(token, corpus):
                return
            if not no_path and shutil.which(token):
                return
            unverified = (token, kind)
            if token in RUNNER_PROGRAMS:
                continue
            break
        if unverified:
            token, kind = unverified
            detail = ("不在仓库配置里（--no-path：未做 PATH 兜底）" if no_path
                      else "不在仓库配置里，也不在当前 PATH 上")
            add(findings, "warn", rel, line_no, "command",
                "无法核实%s `%s`：%s（%s）" % (kind, token, detail, raw.strip()[:70]))

    def walk(blocks):
        for info, body in blocks:
            words = info.split()
            lang = words[0].lower() if words else ""
            if lang in SHELL_LANGS:
                for line_no, raw in body:
                    check_line(line_no, raw)
            else:
                # 普通文本/其他语言块里还可能嵌着子围栏（外层用更长围栏包示例的
                # 写法原先整段逃检，AM-10），递归下钻
                walk(list(iter_fenced_blocks(body)))

    walk(list(iter_fenced_blocks(numbered)))


def check_file(path, root, corpus, opts, findings):
    rel = rel_to(path, root)
    text = read_text(path)
    lines = text.splitlines()

    # 0 编码哨兵：decode("utf-8", "replace") 会把解不开的字节统一换写成 U+FFFD，
    # 一旦出现说明这份文档不是 UTF-8，中文启发式（占位符/日期/禁令）会整体哑火，
    # 不提示的话调用方只会看到"检查全过"的假安心（AM-20）
    bad_chars = text.count("\ufffd")
    if bad_chars:
        add(findings, "warn", rel, 0, "encoding",
            "疑似非 UTF-8（%d 个无法解码字符），中文启发式可能失效" % bad_chars)

    # 1 占位符残留。先剥误报源再匹配（AM-05）：[标签](目标) 链接整体剥掉——链接
    # 文字里的"描述"不是要填的空；行内代码剥掉后再查方括号占位符与 TBD/FIXME；
    # 尖括号占位符按 "raw" 在保留行内代码的行上查（原因见 PLACEHOLDERS 注释）
    for i, line in enumerate(lines, 1):
        no_link = LINK_RE.sub("", line)
        no_code = INLINE_CODE_RE.sub("", no_link)
        for pat, label, where in PLACEHOLDERS:
            if pat.search(no_link if where == "raw" else no_code):
                add(findings, "error", rel, i, "placeholder",
                    "%s 残留：%s" % (label, line.strip()[:70]))
                break

    # 2 相对链接（行内）
    for i, line in enumerate(lines, 1):
        for m in LINK_RE.finditer(line):
            target = m.group(1).strip()
            if not target:
                continue
            if not link_target_exists(path, target):
                add(findings, "error", rel, i, "link",
                    "相对链接的目标不存在：%s" % target)

    # 2b 引用式链接定义（[label]: ./target）——口径与行内链接一致（AM-13）
    for i, line in enumerate(lines, 1):
        m = REF_LINK_DEF_RE.match(line)
        if m and not link_target_exists(path, m.group(2).strip()):
            add(findings, "error", rel, i, "link",
                "引用式链接的目标不存在：%s" % m.group(2).strip())

    # 3/4 命令
    check_commands_for_doc(path, rel, text, root, corpus, findings,
                           no_path=opts.no_path)

    # 5 行数预算
    n = len(lines)
    if n > opts.max_lines:
        add(findings, "warn", rel, 0, "length",
            "共 %d 行，超过 %d 行上限：把细节外移到 docs/ 并在这里留链接"
            % (n, opts.max_lines))
    elif n > opts.budget:
        add(findings, "info", rel, 0, "length",
            "共 %d 行，超过 %d 行的建议预算" % (n, opts.budget))

    # 6 日期戳：全文扫"最后更新"类前缀带的 ISO 日期并取最旧的算陈旧——
    # 只看第一处的话，前文合法、后文陈旧的日期会逃检（AM-16）；
    # 日历上不存在的日期（2026-13-01 之类）单独 warn 列出
    dated = []
    for m in DATE_RE.finditer(text):
        try:
            dated.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            add(findings, "warn", rel, 0, "stale-date",
                "日期 %s-%s-%s 不是有效日历日期，请人工核对" % m.groups())
    if dated:
        oldest = min(dated)
        age = (date.today() - oldest).days
        if age > opts.stale_days:
            add(findings, "warn", rel, 0, "stale-date",
                "「最后更新」是 %s，已 %d 天；要么同步更新内容，要么删掉日期戳"
                % (oldest.isoformat(), age))
    else:
        # 有"最后更新"字样却没有可用 ISO 日期：行内带宽松日期样式的（2026/10/05 之类）
        # 降为 info——格式不是 ISO，脚本核不了新旧，报 error 与事实相反（AM-06）；
        # 确实什么日期都没有才是模板占位符没填
        for i, line in enumerate(lines, 1):
            if not DATE_PREFIX_RE.search(line):
                continue
            if LOOSE_DATE_RE.search(line):
                add(findings, "info", rel, i, "date-format",
                    "非 ISO 日期格式，脚本无法核其新旧：%s" % line.strip()[:70])
            else:
                add(findings, "error", rel, i, "placeholder",
                    "日期戳未填：%s" % line.strip()[:70])
            break

    # 7 裸禁令（启发式，仅列表行）
    for i, line in enumerate(lines, 1):
        bm = BULLET_RE.match(line)
        if not bm:
            continue
        body = bm.group(1)
        if not PROHIBIT_HINT.search(body):
            continue
        if len(body) > 90 or "`" in body or "](" in body \
                or REASON_HINT_CN.search(body) or REASON_HINT_EN.search(body):
            continue
        add(findings, "info", rel, i, "bare-rule",
            "像是没有原因/前置动作的禁令：%s" % body[:60])


def check_sibling_drift(docs, root, findings):
    """同目录 AGENTS.md / CLAUDE.md 共存时比对内容。

    做法②（复制同一份内容）的前提是两份永远同步——这件事交给脚本盯：
    不一致 = warn（同步债已经产生）；完全相同 = info（提示可以只留一份或改指针）。
    比较前把 CRLF 规范化成 LF：Windows 编辑器一存就换行符漂移，"复制同一份内容"
    的口径管的是内容同步不是字节同步，仅 EOL 不同单独给 info（AM-11）。
    一份只是指向另一份的指针（SKILL.md 做法①）不是漂移，报 info 确认即可（AM-19）。
    """
    by_dir = {}
    for d in docs:
        by_dir.setdefault(d.parent, {})[d.name] = d
    for parent in sorted(by_dir):
        files = by_dir[parent]
        if "AGENTS.md" not in files or "CLAUDE.md" not in files:
            continue
        rel = rel_to(files["AGENTS.md"], root)
        a_raw = read_text(files["AGENTS.md"])
        b_raw = read_text(files["CLAUDE.md"])
        a_norm = a_raw.replace("\r\n", "\n").strip()
        b_norm = b_raw.replace("\r\n", "\n").strip()
        if a_norm == b_norm:
            add(findings, "info", rel, 0, "drift",
                "与同目录 CLAUDE.md 内容相同：若目标工具原生认 AGENTS.md，"
                "考虑只留一份，或让 CLAUDE.md 只留一行指针")
            if a_raw != b_raw:
                add(findings, "info", rel, 0, "drift",
                    "与同目录 CLAUDE.md 仅换行符不同（LF/CRLF），内容本身一致")
            continue

        def is_pointer(norm_text):
            # 指针形态：去掉空行后不超过 2 行，且文中提到 AGENTS.md——
            # 这正是 SKILL.md 自荐的做法①，不该按"内容不一致"惩罚它（AM-19）
            body = [ln for ln in norm_text.split("\n") if ln.strip()]
            return len(body) <= 2 and "AGENTS.md" in norm_text

        if is_pointer(a_norm) or is_pointer(b_norm):
            add(findings, "info", rel, 0, "drift",
                "两份文件中有一份只是指向另一份的指针（SKILL.md 做法①），无需同步")
        else:
            add(findings, "warn", rel, 0, "drift",
                "与同目录 CLAUDE.md 内容不一致：要么同步两份，"
                "要么把其中一份换成指向另一份的一行指针")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="AGENTS.md / CLAUDE.md 一致性检查（只读，无第三方依赖）")
    ap.add_argument("--root", default=".", help="仓库根目录（默认当前目录）")
    ap.add_argument("--strict", action="store_true", help="warning 也让退出码为 1")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--budget", type=int, default=200, help="建议行数预算（默认 200）")
    ap.add_argument("--max-lines", type=int, default=500, help="行数上限（默认 500）")
    ap.add_argument("--stale-days", type=int, default=180, help="日期戳陈旧阈值（默认 180 天）")
    ap.add_argument("--no-path", action="store_true",
                    help="CI 用：关闭 PATH 兜底保证本地/CI 结果一致")
    opts = ap.parse_args(argv)

    root = Path(opts.root).expanduser()
    if not root.is_dir():
        sys.stderr.write("不是目录：%s\n" % root)
        return 2
    root = root.resolve()

    docs = discover(root)
    findings = []
    if docs:
        corpus = build_corpus(root)
        for doc in docs:
            check_file(doc, root, corpus, opts, findings)
        check_sibling_drift(docs, root, findings)
    findings.sort(key=lambda f: (f["file"], LEVEL_ORDER.get(f["level"], 9), f["line"]))

    errors = [f for f in findings if f["level"] == "error"]
    warns = [f for f in findings if f["level"] == "warn"]
    infos = [f for f in findings if f["level"] == "info"]

    if opts.json:
        print(json.dumps({
            "root": root.as_posix(),
            "files": [rel_to(d, root) for d in docs],
            "counts": {"error": len(errors), "warn": len(warns), "info": len(infos)},
            "findings": findings,
        }, ensure_ascii=False, indent=2))
    else:
        print("agent-md-check: 根目录 %s，检查 %d 个文件" % (root.as_posix(), len(docs)))
        if not docs:
            print("  未发现 AGENTS.md / CLAUDE.md（确认 --root 是否指向仓库根）")
        for f in findings:
            where = f["file"] + (":%d" % f["line"] if f["line"] else "")
            print("[%s] %s  %s" % (f["level"], where, f["message"]))
        print("汇总: error %d / warn %d / info %d" % (len(errors), len(warns), len(infos)))

    if errors:
        return 1
    if opts.strict and warns:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
