"""表格转图片：正文含 <table> 时走宿主 render.html2png。"""

from __future__ import annotations

import asyncio
from typing import Any

from bs4 import BeautifulSoup

_STYLE = """
body { margin: 0; padding: 12px; background: #ffffff; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }
table { border-collapse: collapse; font-size: 13px; color: #222222; }
th, td { border: 1px solid #d4d4d8; padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: #f4f4f5; font-weight: 600; }
tr:nth-child(even) td { background: #fafafa; }
"""


def has_table(html: str) -> bool:
    if not html or "<table" not in html.lower():
        return False
    return bool(BeautifulSoup(html, "lxml").find("table"))


class TableRenderer:
    """把条目正文里的第一张表格渲染成 PNG（base64）。"""

    def __init__(self, ctx: Any, *, enabled: bool = True, timeout_ms: int = 15000) -> None:
        self._ctx = ctx
        self._enabled = enabled
        self._timeout_ms = timeout_ms

    async def render(self, html: str) -> str:
        if not self._enabled or not has_table(html):
            return ""
        document = (
            f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{_STYLE}</style></head><body>{html}</body></html>"
        )
        try:
            result = await asyncio.wait_for(
                self._ctx.render.html2png(
                    document,
                    selector="body",
                    full_page=True,
                    device_scale_factor=2.0,
                    render_timeout_ms=self._timeout_ms,
                ),
                timeout=self._timeout_ms / 1000 + 5,
            )
        except TimeoutError:
            self._ctx.logger.warning("表格渲染超时，跳过")
            return ""
        except Exception as exc:  # 渲染失败不影响文本推送
            self._ctx.logger.warning("表格渲染失败: %s", exc)
            return ""
        payload = ""
        if isinstance(result, dict):
            payload = str(result.get("image_base64") or "")
        elif isinstance(result, str):
            payload = result
        return payload
