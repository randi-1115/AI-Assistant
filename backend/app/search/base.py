"""联网搜索的最小契约。

只服务 Tavily 与 SerpApi 两家，不做插件注册表，也不为其他引擎预留接口。
两家实现共享 SearchProvider，便于在 chat 层无差别调用。
"""

from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SCRIPT_STYLE = re.compile(r"(?is)<(script|style).*?</\1>")
_BLOCK_END = re.compile(r"(?i)</(p|div|li|h[1-6]|tr|section|article)>")
_BR = re.compile(r"(?i)<br\s*/?>")
MAX_URL_CHARS = 2048


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    # 新闻类结果通常带发布时间；通用网页可能为 None。
    published_date: str | None = None
    # 正文节选（Tavily raw_content 或后续抓取器）；SerpApi 可能为 None。
    content: str | None = None
    domain: str = ""
    # 规范化后的 ISO 日期，供排序/提示词使用。
    published_on: str | None = None
    score: float = 0.0
    provider_rank: int = 0
    provider_score: float | None = None
    authority_tier: int | None = None


class SearchError(Exception):
    """联网搜索的可预期失败。聊天层会捕获并降级，不中断回答。"""


class SearchNotConfigured(SearchError):
    ...


class SearchAuthError(SearchError):
    ...


class SearchRateLimitError(SearchError):
    ...


class SearchTimeoutError(SearchError):
    ...


class SearchUpstreamError(SearchError):
    ...


def clean_text(raw: str | None, max_chars: int) -> str:
    """去掉 HTML 标签/控制字符、压缩空白并截断。搜索结果不可信。"""
    text = _TAG.sub(" ", raw or "")
    text = _CONTROL.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:max_chars]


def clean_content(raw: str | None, max_chars: int) -> str:
    """清洗网页正文：去脚本/样式/标签，保留段落，句末截断。"""
    if not raw or max_chars <= 0:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _SCRIPT_STYLE.sub(" ", text)
    text = _BR.sub("\n", text)
    text = _BLOCK_END.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _CONTROL.sub("", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    cut = max(window.rfind(char) for char in "。！？.!?\n")
    if cut >= max_chars // 2:
        return window[: cut + 1].strip()
    return window.rstrip()


def valid_http_url(raw: str | None) -> str | None:
    """只接受干净的 http/https 绝对链接；任何异常都返回 None，绝不抛出。"""
    if not raw:
        return None
    value = raw.strip()
    if len(value) > MAX_URL_CHARS or _CONTROL.search(value):
        return None
    try:
        parts = urlsplit(value)
        # 访问 hostname/port 会触发畸形 IPv6、非法端口等 ValueError。
        hostname = parts.hostname
        _ = parts.port
    except (ValueError, UnicodeError):
        return None
    if parts.scheme.lower() not in ("http", "https") or not hostname:
        return None
    # 不接受带凭据的链接，避免展示欺骗性 URL。
    if parts.username is not None or parts.password is not None:
        return None
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc, parts.path, parts.query, "")
    )


class SearchProvider(ABC):
    name: str = "unknown"

    @property
    def configured(self) -> bool:
        return True

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout_seconds: float,
        time_range: str | None = None,
        topic: str | None = None,
    ) -> list[SearchResult]:
        ...
