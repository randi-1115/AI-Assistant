"""SerpApi 搜索适配器。

普通问题走 engine=google + organic_results；带“最近/新闻”语义的问题走
engine=google_news + news_results，避免通用网页结果里混入多年旧文。
SerpApi 的 key 只能放在 query string（API 限制）；不要记录完整请求 URL。
"""

from __future__ import annotations

import logging
import re

import httpx

from app.search.base import (
    SearchAuthError,
    SearchNotConfigured,
    SearchProvider,
    SearchRateLimitError,
    SearchResult,
    SearchTimeoutError,
    SearchUpstreamError,
    clean_text,
    valid_http_url,
)

logger = logging.getLogger(__name__)

_TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
_RECENCY_PHRASES = (
    "最近一周",
    "近一周",
    "过去一周",
    "本周",
    "这周",
    "上周",
    "最近一个月",
    "近一个月",
    "过去一个月",
    "本月",
    "这个月",
    "最近一年",
    "今年",
    "过去一年",
    "今天",
    "今日",
    "刚刚",
    "最新",
    "最近的",
    "有什么最新消息",
    "有什么新闻",
    "有什么动态",
    "发生了什么",
    "有哪些",
    "有什么",
    "latest",
    "this week",
    "past week",
    "today",
    "recent",
)
_QUESTION_PUNCT = re.compile(r"[，。！？,.!?：:；;\"'“”‘’《》()（）\[\]【】]")
_WHITESPACE = re.compile(r"\s+")
_NEWS_SUFFIXES = ("新闻", "资讯", "动态", "news")


def clean_news_query(query: str) -> str:
    """Google News 对长问句很敏感；去掉时间/问句词后更容易命中。"""
    cleaned = query
    for phrase in _RECENCY_PHRASES:
        cleaned = cleaned.replace(phrase, " ")
    cleaned = _QUESTION_PUNCT.sub(" ", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    for suffix in _NEWS_SUFFIXES:
        if cleaned.casefold().endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned or query


class SerpApiProvider(SearchProvider):
    name = "serpapi"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://serpapi.com",
        snippet_chars: int = 300,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.snippet_chars = snippet_chars
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout_seconds: float,
        time_range: str | None = None,
        topic: str | None = None,
    ) -> list[SearchResult]:
        if not self.configured:
            raise SearchNotConfigured("SerpApi API Key 未配置")

        # 只有明确新闻语义才走 Google News；entity/realtime/general 仍用 Google，
        # 时间范围通过 tbs 表达，避免“有时间范围就变新闻”的误判。
        use_news = topic == "news"
        params: dict[str, object] = {
            "api_key": self.api_key,
            "num": max_results,
        }
        if use_news:
            params.update(
                {
                    "engine": "google_news",
                    "q": clean_news_query(query),
                    "hl": "zh-cn",
                    "gl": "cn",
                }
            )
        else:
            params.update({"engine": "google", "q": query})
            if time_range and time_range in _TBS:
                params["tbs"] = _TBS[time_range]

        url = f"{self.base_url}/search.json"

        try:
            if self._client is not None:
                response = await self._client.get(
                    url, params=params, timeout=timeout_seconds
                )
            else:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    response = await client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise SearchTimeoutError("联网搜索超时") from exc
        except httpx.HTTPError as exc:
            raise SearchUpstreamError("联网搜索请求失败") from exc

        if response.status_code in (401, 403):
            raise SearchAuthError("SerpApi 鉴权失败，请检查 SERPAPI_API_KEY")
        if response.status_code == 429:
            raise SearchRateLimitError("SerpApi 请求过于频繁，请稍后再试")
        if response.status_code >= 400:
            raise SearchUpstreamError(f"SerpApi 返回错误（HTTP {response.status_code}）")

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchUpstreamError("SerpApi 返回了无法解析的响应") from exc
        if not isinstance(data, dict):
            raise SearchUpstreamError("SerpApi 返回了无法解析的响应")

        error = data.get("error")
        if error:
            # 上游原文只进日志，用户只看到泛化提示，避免泄露账号/参数细节。
            logger.warning("SerpApi 返回 error=%s", clean_text(str(error), 300))
            lowered = str(error).lower()
            if "api key" in lowered or "unauthorized" in lowered or "invalid" in lowered:
                raise SearchAuthError("SerpApi 鉴权失败，请检查 SERPAPI_API_KEY")
            raise SearchUpstreamError("SerpApi 返回错误，请检查额度或参数")

        items = data.get("news_results") if use_news else data.get("organic_results")
        if not isinstance(items, list):
            # Google News 偶尔把结果放在 organic_results，反之亦然。
            fallback_key = "organic_results" if use_news else "news_results"
            items = data.get(fallback_key)
            if not isinstance(items, list):
                items = []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_title = item.get("title")
            raw_link = item.get("link")
            raw_snippet = item.get("snippet")
            raw_date = item.get("date")
            title = clean_text(raw_title if isinstance(raw_title, str) else "", 200)
            url_value = valid_http_url(raw_link if isinstance(raw_link, str) else None)
            snippet = clean_text(
                raw_snippet if isinstance(raw_snippet, str) else "",
                self.snippet_chars,
            )
            published_date = clean_text(
                raw_date if isinstance(raw_date, str) else "", 100
            ) or None
            if title and url_value:
                results.append(
                    SearchResult(
                        title=title,
                        url=url_value,
                        snippet=snippet,
                        published_date=published_date,
                    )
                )
            if len(results) >= max_results:
                break
        return results
