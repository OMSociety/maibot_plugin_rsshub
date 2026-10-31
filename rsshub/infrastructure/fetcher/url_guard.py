"""订阅 URL 校验与 SSRF 防护。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

from ...errors import InvalidFeedUrlError

_ALLOWED_SCHEMES = ("http", "https")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or (ip.version == 6 and ip.ipv4_mapped is not None and _is_blocked_ip(ip.ipv4_mapped))
    )


def resolve_and_check(address: str) -> None:
    """解析域名并检查目标地址；任何解析失败或命中拦截网段都抛错。"""
    try:
        infos = socket.getaddrinfo(address, None)
    except OSError as exc:
        raise InvalidFeedUrlError(f"无法解析域名: {address}") from exc
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise InvalidFeedUrlError(f"订阅地址指向内网/保留网段，已拦截: {address} -> {raw}")


def normalize_feed_url(raw_url: str, *, allow_private: bool, rsshub_base_url: str) -> str:
    """校验并规范化订阅 URL；支持 RSSHub 短路由（/path 拼 base_url）。"""
    url = (raw_url or "").strip()
    if not url:
        raise InvalidFeedUrlError("订阅链接不能为空")

    if url.startswith("/"):
        base = (rsshub_base_url or "").strip().rstrip("/")
        if not base:
            raise InvalidFeedUrlError("未配置 RSSHub 实例地址，无法解析短路由")
        url = f"{base}{url}"

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise InvalidFeedUrlError("订阅链接必须以 http:// 或 https:// 开头")
    if not parsed.netloc:
        raise InvalidFeedUrlError("订阅链接缺少域名")

    host = parsed.hostname or ""
    if not allow_private:
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise InvalidFeedUrlError("订阅地址指向本机地址，已拦截")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            resolve_and_check(host)
        else:
            if _is_blocked_ip(ip):
                raise InvalidFeedUrlError(f"订阅地址指向内网/保留网段，已拦截: {host}")

    # 去掉片段与空查询，路径末尾斜杠保留（部分站点区分）
    normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, ""))
    return normalized


def strip_tracking_params(url: str, tracking: frozenset[str]) -> str:
    """剔除链接里的跟踪参数，用于去重比对。"""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = [
        pair
        for pair in parsed.query.split("&")
        if pair
        and pair.split("=", 1)[0].lower() not in tracking
        and not pair.split("=", 1)[0].lower().startswith("utm_")
    ]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, "&".join(kept), parsed.fragment))
