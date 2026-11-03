"""HTTP 抓取：条件请求、代理、超时与重定向复核。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from ...errors import FeedFetchError, FeedTimeoutError
from .url_guard import guard_fetch_target

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 3


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    headers: dict[str, str]
    not_modified: bool = False


def socksio_available() -> bool:
    """socks 代理依赖是否就绪（httpx 的 socks 支持需要单独安装 socksio）。"""
    try:
        import socksio  # noqa: F401
    except ImportError:
        return False
    return True


class HttpClient:
    """按配置构造的异步 HTTP 客户端，生命周期由调用方管理。"""

    def __init__(
        self,
        *,
        timeout: float,
        user_agent: str,
        proxy: str = "",
        allow_private: bool = False,
    ) -> None:
        self._timeout = timeout
        self._user_agent = user_agent
        self._proxy = proxy.strip()
        self._allow_private = allow_private
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict = {
                "timeout": self._timeout,
                # 重定向自己跟：每一跳都要过 url_guard 复核落点，不能让 302 把请求带进内网。
                "follow_redirects": False,
                # 代理只由插件配置决定：继承宿主进程的 HTTP_PROXY / NO_PROXY 会让
                # 「关掉代理」实际仍走系统代理，NO_PROXY 里的 "[::1]" 还会让 httpx 解析失败。
                "trust_env": False,
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
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> FetchResult:
        client = await self._get_client()
        condition_headers: dict[str, str] = {}
        if etag:
            condition_headers["If-None-Match"] = etag
        if last_modified:
            condition_headers["If-Modified-Since"] = last_modified
        if headers:
            condition_headers.update(headers)
        request_timeout = self._timeout if timeout is None else timeout

        current = url
        response: httpx.Response | None = None
        for hop in range(_MAX_REDIRECTS + 1):
            current = await guard_fetch_target(current, allow_private=self._allow_private)
            try:
                response = await client.get(
                    current,
                    headers=condition_headers if hop == 0 else {},
                    timeout=request_timeout,
                )
            except httpx.TimeoutException as exc:
                raise FeedTimeoutError(f"请求超时: {current}") from exc
            except httpx.HTTPError as exc:
                raise FeedFetchError(f"网络错误: {exc}") from exc

            location = response.headers.get("location")
            if response.status_code in _REDIRECT_STATUSES and location:
                if hop == _MAX_REDIRECTS:
                    raise FeedFetchError(f"重定向次数过多: {url}")
                current = urljoin(current, location)
                continue
            break

        assert response is not None
        if response.status_code == 304:
            return FetchResult(
                url=str(response.url), status=304, content=b"", headers=dict(response.headers), not_modified=True
            )
        if response.status_code != 200:
            raise FeedFetchError(f"抓取失败: HTTP {response.status_code} ({current})")
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
