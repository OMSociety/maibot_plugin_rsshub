"""插件自定义异常。"""

from __future__ import annotations


class RsshubError(Exception):
    """插件异常基类。"""


class FeedFetchError(RsshubError):
    """抓取订阅源失败（网络错误、非 2xx、解析失败）。"""


class FeedTimeoutError(FeedFetchError):
    """抓取超时：订阅源或图片文件在时间预算内没有响应完。"""


class InvalidFeedUrlError(RsshubError):
    """订阅 URL 非法（协议不支持、指向内网、SSRF 拦截）。"""


class SubscriptionNotFoundError(RsshubError):
    """订阅不存在或不属于当前会话。"""


class UnknownHandlerError(RsshubError):
    """不存在的 handler 名称。"""
