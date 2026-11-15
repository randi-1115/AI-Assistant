"""Tavily / SerpApi 适配器：解析、鉴权、错误映射、URL 安全。"""

from __future__ import annotations

import json

import httpx
import pytest

from app.search.base import (
    SearchAuthError,
    SearchNotConfigured,
    SearchRateLimitError,
    SearchTimeoutError,
    SearchUpstreamError,
    clean_text,
    valid_http_url,
)
from app.search.serpapi import SerpApiProvider
from app.search.tavily import TavilyProvider


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_tavily_parses_results_and_uses_bearer_header() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "A <b>title</b>",
                        "url": "https://example.com/a#fragment",
                        "content": "<p>hello</p>",
                    }
                ]
            },
        )

    async with make_client(handler) as client:
        provider = TavilyProvider("test-key", snippet_chars=50, client=client)
        results = await provider.search("query", max_results=3, timeout_seconds=1)

    assert results[0].title == "A title"
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "hello"
    assert captured["auth"] == "Bearer test-key"
    assert "test-key" not in json.dumps(captured["body"])
    assert "test-key" not in str(captured["url"])


async def test_tavily_passes_news_filters_and_parses_date() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "新闻",
                        "url": "https://example.com/news",
                        "content": "摘要",
                        "published_date": "Tue, 10 Nov 2026 01:00:00 GMT",
                    }
                ]
            },
        )

    async with make_client(handler) as client:
        provider = TavilyProvider("k", client=client)
        results = await provider.search(
            "最近一周AI新闻",
            max_results=3,
            timeout_seconds=1,
            time_range="week",
            topic="news",
        )

    assert captured["body"]["time_range"] == "week"
    assert captured["body"]["topic"] == "news"
    assert results[0].published_date == "Tue, 10 Nov 2026 01:00:00 GMT"


async def test_tavily_parses_raw_content_and_truncates() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "正文测试",
                        "url": "https://example.com/article",
                        "content": "摘要",
                        "raw_content": "<p>First.</p><script>bad()</script><p>Second.</p>",
                    }
                ]
            },
        )

    async with make_client(handler) as client:
        provider = TavilyProvider("k", content_chars=6, client=client)
        results = await provider.search("q", max_results=1, timeout_seconds=1)

    assert captured["body"]["include_raw_content"] is True
    assert results[0].content == "First."
    assert "script" not in (results[0].content or "")


async def test_tavily_can_disable_raw_content() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": []})

    async with make_client(handler) as client:
        provider = TavilyProvider("k", include_raw_content=False, client=client)
        await provider.search("q", max_results=1, timeout_seconds=1)

    assert captured["body"]["include_raw_content"] is False


async def test_tavily_drops_invalid_items_and_respects_limit() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "ok", "url": "https://a.example", "content": "1"},
                    {"title": "relative", "url": "/no", "content": "2"},
                    {"title": "js", "url": "javascript:alert(1)", "content": "3"},
                    {"title": "", "url": "https://b.example", "content": "4"},
                    {"title": "ok2", "url": "https://c.example", "content": "5"},
                    {"title": "ok3", "url": "https://d.example", "content": "6"},
                ]
            },
        )

    async with make_client(handler) as client:
        provider = TavilyProvider("k", client=client)
        results = await provider.search("q", max_results=2, timeout_seconds=1)

    assert [result.title for result in results] == ["ok", "ok2"]


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, SearchAuthError),
        (403, SearchAuthError),
        (429, SearchRateLimitError),
        (432, SearchRateLimitError),
        (500, SearchUpstreamError),
    ],
)
async def test_tavily_status_mapping(status: int, error_type: type[Exception]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="upstream body should not leak")

    async with make_client(handler) as client:
        provider = TavilyProvider("k", client=client)
        with pytest.raises(error_type) as caught:
            await provider.search("q", max_results=1, timeout_seconds=1)

    assert "upstream body should not leak" not in str(caught.value)


async def test_tavily_timeout_and_bad_json() -> None:
    def timeout_handler(_: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    async with make_client(timeout_handler) as client:
        with pytest.raises(SearchTimeoutError):
            await TavilyProvider("k", client=client).search(
                "q", max_results=1, timeout_seconds=1
            )

    def bad_json_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async with make_client(bad_json_handler) as client:
        with pytest.raises(SearchUpstreamError):
            await TavilyProvider("k", client=client).search(
                "q", max_results=1, timeout_seconds=1
            )

    def list_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with make_client(list_handler) as client:
        with pytest.raises(SearchUpstreamError):
            await TavilyProvider("k", client=client).search(
                "q", max_results=1, timeout_seconds=1
            )


async def test_tavily_not_configured() -> None:
    with pytest.raises(SearchNotConfigured):
        await TavilyProvider("").search("q", max_results=1, timeout_seconds=1)


async def test_serpapi_parses_results_and_params() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "organic_results": [
                    {
                        "title": "  Example  ",
                        "link": "https://example.com/a#x",
                        "snippet": "snip",
                    }
                ]
            },
        )

    async with make_client(handler) as client:
        provider = SerpApiProvider("serp-key", snippet_chars=10, client=client)
        results = await provider.search("hello world", max_results=4, timeout_seconds=1)

    assert results[0].title == "Example"
    assert results[0].url == "https://example.com/a"
    assert captured["engine"] == "google"
    assert captured["q"] == "hello world"
    assert captured["num"] == "4"
    assert captured["api_key"] == "serp-key"


async def test_serpapi_error_on_200() -> None:
    def auth_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "Invalid API key"})

    async with make_client(auth_handler) as client:
        with pytest.raises(SearchAuthError):
            await SerpApiProvider("k", client=client).search(
                "q", max_results=1, timeout_seconds=1
            )

    def generic_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "internal quota detail"})

    async with make_client(generic_handler) as client:
        with pytest.raises(SearchUpstreamError) as caught:
            await SerpApiProvider("k", client=client).search(
                "q", max_results=1, timeout_seconds=1
            )
    assert "internal quota detail" not in str(caught.value)


async def test_serpapi_general_time_range_uses_tbs() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json={"organic_results": []})

    async with make_client(handler) as client:
        provider = SerpApiProvider("k", client=client)
        await provider.search(
            "DeepSeek 最新模型",
            max_results=3,
            timeout_seconds=1,
            time_range="week",
            topic=None,
        )

    assert captured["engine"] == "google"
    assert captured["tbs"] == "qdr:w"


async def test_serpapi_uses_google_news_for_news_queries() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "news_results": [
                    {
                        "title": "新闻标题",
                        "link": "https://example.com/news",
                        "date": "11/12/2026",
                        "snippet": "摘要",
                    }
                ]
            },
        )

    async with make_client(handler) as client:
        provider = SerpApiProvider("k", client=client)
        results = await provider.search(
            "最近一周AI圈有什么新闻",
            max_results=5,
            timeout_seconds=1,
            time_range="week",
            topic="news",
        )

    assert captured["engine"] == "google_news"
    assert captured["q"] == "AI圈"
    assert captured["hl"] == "zh-cn"
    assert results[0].published_date == "11/12/2026"


def test_clean_news_query() -> None:
    from app.search.serpapi import clean_news_query

    assert clean_news_query("最近一周AI圈有什么新闻") == "AI圈"
    assert clean_news_query("今天有什么AI新闻") == "AI"
    assert clean_news_query("AI") == "AI"


async def test_serpapi_empty_results() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"organic_results": []})

    async with make_client(handler) as client:
        results = await SerpApiProvider("k", client=client).search(
            "q", max_results=3, timeout_seconds=1
        )
    assert results == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com/a?b=1#frag", "https://example.com/a?b=1"),
        ("javascript:alert(1)", None),
        ("data:text/html,x", None),
        ("/relative", None),
        ("https://user:pass@example.com/a", None),
        ("http://[::1", None),
        ("", None),
    ],
)
def test_valid_http_url(raw: str, expected: str | None) -> None:
    assert valid_http_url(raw) == expected


def test_clean_text_removes_tags_and_controls() -> None:
    assert clean_text("<b>a</b>\x00b\nc", 100) == "a b c"
