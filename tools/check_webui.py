"""WebUI 声明离线校验：webui.json 与 web_api.py 的 @API 是否一一对应。

只依赖标准库，供本地与 CI 使用。检查项：
1. webui.json 可解析，schema_version 为 2，页面入口在插件目录内且全部 custom HTML 合计不超过 8 MiB；
2. custom 页面声明 custom_pages 能力，layout 为 document 或 viewport；
3. 声明的查询/动作与 web_api.py 里带 @API 装饰器的方法同名同参数（缺一个或参数不一致都报错）；
4. required_capabilities 只使用宿主认识的四个能力名。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

KNOWN_CAPABILITIES = {"declarative_basic", "custom_pages", "image_upload", "structured_arguments"}
MAX_TOTAL_DOCUMENT_BYTES = 8 * 1024 * 1024
API_SOURCE = Path("rsshub/interfaces/web_api.py")


def load_declaration(root: Path) -> dict:
    path = root / "webui.json"
    if not path.is_file():
        raise SystemExit("缺少 webui.json")
    return json.loads(path.read_text(encoding="utf-8"))


def api_signatures(root: Path) -> dict[str, list[str]]:
    source = root / API_SOURCE
    if not source.is_file():
        raise SystemExit(f"缺少 {API_SOURCE}")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_decorator_name(item) == "API" for item in node.decorator_list):
            continue
        args = node.args
        positional = [*args.posonlyargs, *args.args][1:]  # 去掉 self
        positional += args.kwonlyargs
        found[node.name] = [arg.arg for arg in positional]
    return found


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def collect_bindings(declaration: dict) -> dict[str, list[str]]:
    bindings: dict[str, list[str]] = {}
    for page in declaration.get("pages", []):
        for kind in ("queries", "actions"):
            items = page.get(kind) or {}
            if not isinstance(items, dict):
                raise SystemExit(f"webui.json 的 {kind} 应该是对象（绑定名 -> 声明）")
            for spec in items.values():
                if not isinstance(spec, dict):
                    raise SystemExit(f"webui.json 的 {kind} 绑定项应为对象")
                api = spec.get("api")
                if not api:
                    raise SystemExit(f"webui.json 的 {kind} 绑定缺少 api 字段")
                parameters = spec.get("parameters") or {}
                bindings[api] = list(parameters)
    return bindings


def main(root: Path) -> int:
    problems: list[str] = []
    declaration = load_declaration(root)

    if declaration.get("schema_version") != 2:
        problems.append(f"schema_version 应为 2，实际为 {declaration.get('schema_version')!r}")

    capabilities = set(declaration.get("required_capabilities", []))
    unknown = capabilities - KNOWN_CAPABILITIES
    if unknown:
        problems.append(f"required_capabilities 含未知能力: {sorted(unknown)}")

    total_document_bytes = 0
    for page in declaration.get("pages", []):
        if page.get("mode") == "custom":
            if "custom_pages" not in capabilities:
                problems.append(f"custom 页面 {page.get('id', '')} 未声明 custom_pages 能力")
            if page.get("layout", "document") not in {"document", "viewport"}:
                problems.append(f"custom 页面 {page.get('id', '')} 的 layout 非法")
        entry = page.get("entry")
        if not entry:
            continue
        entry_path = Path(entry)
        if (
            entry_path.is_absolute()
            or entry_path.anchor
            or ".." in entry_path.parts
            or entry_path.suffix.lower() != ".html"
        ):
            problems.append(f"页面入口必须是插件内的相对 HTML 路径: {entry}")
            continue
        document = root / entry_path
        if any(part.is_symlink() or part.is_junction() for part in [document, *document.parents] if part != root):
            problems.append(f"页面入口路径不能包含链接: {entry}")
            continue
        if not document.resolve().is_relative_to(root) or not document.is_file():
            problems.append(f"页面入口不存在或超出插件目录: {entry}")
            continue
        if page.get("mode") == "custom":
            total_document_bytes += document.stat().st_size
    if total_document_bytes > MAX_TOTAL_DOCUMENT_BYTES:
        problems.append(f"custom HTML 合计超过 8 MiB（{total_document_bytes} 字节）")

    declared = collect_bindings(declaration)
    code = api_signatures(root)

    for name in sorted(set(code) - set(declared)):
        problems.append(f"{API_SOURCE} 里的 @API {name} 未在 webui.json 声明")
    for name in sorted(set(declared) - set(code)):
        problems.append(f"webui.json 声明了 {name}，但 {API_SOURCE} 没有同名 @API")
    for name in sorted(set(code) & set(declared)):
        if sorted(code[name]) != sorted(declared[name]):
            problems.append(f"{name} 参数不一致: 代码 {code[name]} / 声明 {declared[name]}")

    if problems:
        for item in problems:
            print(f"ERROR {item}")
        return 1

    print(f"webui 声明校验通过：{len(code)} 个 API 与 webui.json 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()))
