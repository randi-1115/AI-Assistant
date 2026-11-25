"""联网搜索工具：可用性探测与 SearchOutcome -> ToolResult 的映射。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.search.base import SearchAuthError, SearchProvider, SearchResult
from app.search.service import SearchService
from app.tools.base import ToolContext
from app.tools.web_search import UNAVAILABLE_REASON, WebSearchArgs, WebSearchTool

CONTEXT = ToolContext(user_id=1)


def make_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        database_url="mysql+aiomysql://user:pass@localhost/db",
        search_provider="tavily",
        tavily_api_key="test-key",
        search_llm_planning_enabled=False,
        **overrides,
    )


def result(
    title: str = "示例标题",
    url: str = "https://example.com/a",
    snippet: str = "网页摘要",
    content: str | None = "网页正文",
) -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        snippet=snippet,
        content=content,
        domain="example.com",
    )


class FakeProvider(SearchProvider):
    def __init__(
        self,
        *,
        results: list[SearchResult] | None = None,
        error: Exception | None = None,
        configured: bool = True,
    ) -> None:
        self.name = "fake"
        self.results = results or []
        self.error = error
        self._configured = configured
        self.calls = 0
        self.last_query: str | None = None

    @property
    def configured(self) -> bool:
        return self._configured

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout_seconds: float,
        time_range: str | None = None,
        topic: str | None = None,
    ) -> list[SearchResult]:
        self.calls += 1
        self.last_query = query
        if self.error is not None:
            raise self.error
        return list(self.results)[:max_results]


def tool_with(provider: FakeProvider, **settings_overrides) -> WebSearchTool:
    settings = make_settings(**settings_overrides)
    return WebSearchTool(settings, SearchService(settings, provider=provider))


def test_configured_provider_is_available() -> None:
    tool = tool_with(FakeProvider(results=[result()]))
    assert tool.is_available(CONTEXT) is True
    assert tool.unavailable_reason(CONTEXT) is None


def test_unconfigured_provider_is_unavailable_with_reason() -> None:
    tool = tool_with(FakeProvider(configured=False))
    assert tool.is_available(CONTEXT) is False
    assert tool.unavailable_reason(CONTEXT) == UNAVAILABLE_REASON


async def test_ok_outcome_returns_block_and_sources() -> None:
    tool = tool_with(FakeProvider(results=[result()]))
    outcome = await tool.run(WebSearchArgs(query="示例"), CONTEXT)

    assert outcome.status == "success"
    assert "<web_results>" in outcome.content
    assert "https://example.com/a" in outcome.content
    assert outcome.sources[0]["kind"] == "web"
    assert outcome.sources[0]["url"] == "https://example.com/a"
    assert outcome.sources[0]["domain"] == "example.com"
    assert outcome.data["provider"] == "fake"
    assert outcome.data["result_count"] == 1


async def test_empty_outcome_maps_to_empty() -> None:
    tool = tool_with(FakeProvider(results=[]))
    outcome = await tool.run(WebSearchArgs(query="示例"), CONTEXT)

    assert outcome.status == "empty"
    assert outcome.sources == ()
    assert "没有找到" in outcome.content


async def test_upstream_error_maps_to_unavailable() -> None:
    tool = tool_with(FakeProvider(error=SearchAuthError("鉴权失败")))
    outcome = await tool.run(WebSearchArgs(query="示例"), CONTEXT)

    assert outcome.status == "unavailable"
    assert "不可用" in outcome.content
    assert "鉴权失败" in outcome.content


async def test_query_is_passed_through_with_mode_auto() -> None:
    provider = FakeProvider(results=[result()])
    tool = tool_with(provider)
    await tool.run(WebSearchArgs(query="  最新 新闻  "), CONTEXT)
    # 宽泛新闻会被查询规划拆成多个子查询，所以只断言确实调用了且带上了关键词。
    assert provider.calls >= 1
    assert "最新" in (provider.last_query or "")


async def test_snippet_respects_settings_cap() -> None:
    provider = FakeProvider(results=[result(snippet="长" * 500)])
    tool = tool_with(provider, search_snippet_chars=20)
    outcome = await tool.run(WebSearchArgs(query="示例"), CONTEXT)
    assert len(outcome.sources[0]["snippet"]) <= 20


async def test_content_respects_context_budget() -> None:
    provider = FakeProvider(
        results=[result(content="正文" * 500, snippet="摘要" * 200)]
    )
    tool = tool_with(
        provider, search_context_max_chars=400, search_max_results=3, search_fetch_results=5
    )
    outcome = await tool.run(WebSearchArgs(query="示例"), CONTEXT)
    assert len(outcome.content) <= 400


async def test_data_records_latency_and_cache_fields() -> None:
    tool = tool_with(FakeProvider(results=[result()]))
    outcome = await tool.run(WebSearchArgs(query="示例"), CONTEXT)
    assert set(outcome.data) == {
        "provider",
        "intent",
        "latency_ms",
        "calls",
        "cached",
        "result_count",
    }


@pytest.mark.parametrize("query", ["", "   "])
def test_blank_query_rejected(query: str) -> None:
    with pytest.raises(ValidationError):
        WebSearchArgs(query=query)
