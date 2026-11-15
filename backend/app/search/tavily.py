"""Tavily 搜索适配器。

官方鉴权：Authorization: Bearer tvly-...；不把 key 放进 body 或 URL。
文档：https://docs.tavily.com/documentation/api-headers
"""

from __future__ import annotations

import httpx

from app.search.base import (
    SearchAuthError,
    SearchNotConfigured,
    SearchProvider,
    SearchRateLimitError,
    SearchResult,
    SearchTimeoutError,
    SearchUpstreamError,
    clean_content,
    clean_text,
    valid_http_url,
)


class TavilyProvider(SearchProvider):
    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.tavily.com",
        snippet_chars: int = 300,
        include_raw_content: bool = True,
        content_chars: int = 1200,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.snippet_chars = snippet_chars
        self.include_raw_content = include_raw_content
        self.content_chars = content_chars
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
            raise SearchNotConfigured("Tavily API Key 未配置")

        payload: dict[str, object] = {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": self.include_raw_content,
        }
        if time_range:
            payload["time_range"] = time_range
        if topic:
            payload["topic"] = topic
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/search"

        try:
            if self._client is not None:
                response = await self._client.post(
                    url, json=payload, headers=headers, timeout=timeout_seconds
                )
            else:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise SearchTimeoutError("联网搜索超时") from exc
        except httpx.HTTPError as exc:
            raise SearchUpstreamError("联网搜索请求失败") from exc

        if response.status_code in (401, 403):
            raise SearchAuthError("Tavily 鉴权失败，请检查 TAVILY_API_KEY")
        if response.status_code in (429, 432):
            raise SearchRateLimitError("Tavily 请求过于频繁或额度不足，请稍后再试")
        if response.status_code >= 400:
            raise SearchUpstreamError(f"Tavily 返回错误（HTTP {response.status_code}）")

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchUpstreamError("Tavily 返回了无法解析的响应") from exc
        if not isinstance(data, dict):
            raise SearchUpstreamError("Tavily 返回了无法解析的响应")

        items = data.get("results")
        if not isinstance(items, list):
            items = []

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_title = item.get("title")
            raw_url = item.get("url")
            raw_content = item.get("content")
            title = clean_text(raw_title if isinstance(raw_title, str) else "", 200)
            url_value = valid_http_url(raw_url if isinstance(raw_url, str) else None)
            snippet = clean_text(
                raw_content if isinstance(raw_content, str) else "",
                self.snippet_chars,
            )
            raw_body = item.get("raw_content")
            body = clean_content(
                raw_body if isinstance(raw_body, str) else None,
                self.content_chars,
            ) or None
            raw_date = item.get("published_date")
            published_date = clean_text(
                raw_date if isinstance(raw_date, str) else "", 100
            ) or None
            raw_score = item.get("score")
            provider_score = (
                float(raw_score)
                if isinstance(raw_score, (int, float))
                else None
            )
            if title and url_value:
                results.append(
                    SearchResult(
                        title=title,
                        url=url_value,
                        snippet=snippet,
                        published_date=published_date,
                        content=body,
                        provider_score=provider_score,
                    )
                )
            if len(results) >= max_results:
                break
        return results
