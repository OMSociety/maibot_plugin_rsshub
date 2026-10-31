"""HTTP 抓取：条件请求 + 代理 + 超时。

用宿主基线自带的 httpx（httpx[socks] 支持 socks5 代理），不再引入 aiohttp-socks。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ...errors import FeedFetchError


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    headers: dict[str, str]
    not_modified: bool = False


class HttpClient:
    """按配置构造的异步 HTTP 客户端，生命周期由调用方管理。"""

    def __init__(
        self,
        *,
        timeout: float,
        user_agent: str,
        proxy: str = "",
    ) -> None:
        self._timeout = timeout
        self._user_agent = user_agent
        self._proxy = proxy.strip()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict = {
                "timeout": self._timeout,
                "follow_redirects": True,
                "headers": {
                    "User-Agent": self._user_agent,
                    "Accept": "application/rss+xml, application/atom+xml, "
                    "application/feed+json, application/json, text/xml, */*",
                },
            }
            if self._proxy:
                kwargs["proxy"] = self._proxy
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        client = await self._get_client()
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            response = await client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise FeedFetchError(f"请求超时: {url}") from exc
        except httpx.HTTPError as exc:
            raise FeedFetchError(f"网络错误: {exc}") from exc

        if response.status_code == 304:
            return FetchResult(
                url=str(response.url), status=304, content=b"", headers=dict(response.headers), not_modified=True
            )
        if response.status_code != 200:
            raise FeedFetchError(f"抓取失败: HTTP {response.status_code} ({url})")
        return FetchResult(
            url=str(response.url),
            status=response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
