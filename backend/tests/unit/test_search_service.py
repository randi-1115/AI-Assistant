"""搜索引擎选择、fallback、去重与失败降级。"""

from __future__ import annotations

from app.core.config import Settings
from app.search import search_available
from app.search.base import (
    SearchAuthError,
    SearchProvider,
    SearchResult,
    SearchTimeoutError,
    SearchUpstreamError,
)
from app.search.serpapi import SerpApiProvider
from app.search.service import (
    FallbackSearchProvider,
    SearchService,
    build_search_provider,
    dedupe_results,
    detect_time_range,
    detect_topic,
    normalize_query,
)
from app.search.tavily import TavilyProvider


def make_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        database_url="mysql+aiomysql://user:pass@localhost/db",
        **overrides,
    )


def result(title: str = "标题", url: str = "https://example.com/a", snippet: str = "摘要") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


class FakeProvider(SearchProvider):
    def __init__(
        self,
        *,
        name: str = "fake",
        results: list[SearchResult] | None = None,
        error: Exception | None = None,
        configured: bool = True,
    ) -> None:
        self.name = name
        self.results = results or []
        self.error = error
        self._configured = configured
        self.calls = 0
        self.last_query: str | None = None
        self.last_max_results: int | None = None
        self.last_time_range: str | None = None
        self.last_topic: str | None = None

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
        self.last_max_results = max_results
        self.last_time_range = time_range
        self.last_topic = topic
        if self.error is not None:
            raise self.error
        return list(self.results)[:max_results]


async def test_off_does_not_call_provider() -> None:
    provider = FakeProvider(results=[result()])
    service = SearchService(make_settings(), provider=provider)

    outcome = await service.search("问题", mode="off")

    assert outcome.status == "disabled"
    assert provider.calls == 0


async def test_unconfigured_provider_is_unavailable() -> None:
    no_provider = SearchService(make_settings(), provider=None, resolve_provider=False)
    assert (await no_provider.search("q", mode="auto")).status == "unavailable"

    unconfigured = SearchService(
        make_settings(), provider=FakeProvider(configured=False)
    )
    outcome = await unconfigured.search("q", mode="auto")
    assert outcome.status == "unavailable"
    assert "未配置" in (outcome.message or "")


async def test_ok_outcome() -> None:
    provider = FakeProvider(results=[result("a"), result("b", url="https://b.example")])
    service = SearchService(make_settings(search_max_results=5), provider=provider)

    outcome = await service.search("问题", mode="auto")

    assert outcome.status == "ok"
    assert len(outcome.results) == 2
    assert outcome.provider == "fake"
    assert "2 条结果" in (outcome.message or "")


async def test_empty_outcome() -> None:
    provider = FakeProvider(results=[])
    service = SearchService(make_settings(), provider=provider)

    outcome = await service.search("问题", mode="auto")

    assert outcome.status == "empty"
    assert outcome.results == ()
    assert outcome.provider == "fake"


async def test_search_error_is_unavailable() -> None:
    provider = FakeProvider(error=SearchAuthError("鉴权失败"))
    service = SearchService(make_settings(), provider=provider)

    outcome = await service.search("问题", mode="auto")

    assert outcome.status == "unavailable"
    assert outcome.message == "鉴权失败"


async def test_unexpected_error_does_not_escape() -> None:
    provider = FakeProvider(error=RuntimeError("boom"))
    service = SearchService(make_settings(), provider=provider)

    outcome = await service.search("问题", mode="auto")

    assert outcome.status == "unavailable"
    assert outcome.message == "联网搜索暂时不可用"


async def test_query_is_normalized_and_truncated() -> None:
    provider = FakeProvider(results=[result()])
    service = SearchService(make_settings(), provider=provider)
    raw = "  a \n b " + "x" * 500

    await service.search(raw, mode="auto")

    assert provider.last_query == ("a b " + "x" * 500)[:400]
    assert len(provider.last_query or "") == 400
    assert normalize_query("  a \n b  ") == "a b"


def test_detect_time_range_and_topic() -> None:
    assert detect_time_range("今天有什么新闻") == "day"
    assert detect_time_range("最近一周AI圈有什么新闻") == "week"
    assert detect_time_range("本月AI进展") == "month"
    assert detect_time_range("今年的大模型") == "year"
    assert detect_time_range("解释一下 Transformer") is None

    assert detect_topic("最近一周AI圈有什么新闻") == "news"
    assert detect_topic("解释一下 Transformer") is None


async def test_entity_query_passes_general_topic() -> None:
    provider = FakeProvider(results=[result()])
    service = SearchService(make_settings(), provider=provider)

    await service.search("DeepSeek 最近发布了什么模型", mode="auto")

    assert provider.last_time_range == "month"
    assert provider.last_topic is None


async def test_realtime_query_uses_day_and_warns() -> None:
    provider = FakeProvider(results=[result()])
    service = SearchService(make_settings(), provider=provider)

    outcome = await service.search("英伟达最新股价", mode="auto")

    assert provider.last_time_range == "day"
    assert outcome.intent == "realtime_data"
    assert "延迟" in (outcome.message or "")


async def test_search_passes_freshness_filters_to_provider() -> None:
    provider = FakeProvider(results=[result()])
    service = SearchService(make_settings(), provider=provider)

    outcome = await service.search("最近一周AI圈有什么新闻", mode="auto")

    assert outcome.status == "ok"
    assert provider.last_time_range == "week"
    assert provider.last_topic == "news"
    assert "时间范围：week" in (outcome.message or "")


def test_dedupe_by_canonical_url() -> None:
    results = [
        result("first", "https://example.com/a?utm_source=x"),
        result("dup1", "https://example.com/a"),
        result("dup2", "https://example.com/a/#gclid=1"),
        result("dup3", "https://www.example.com/a"),
        result("other", "https://example.com/b"),
    ]

    deduped = dedupe_results(results, limit=5)

    assert [item.title for item in deduped] == ["first", "other"]


async def test_dedupe_respects_limit() -> None:
    provider = FakeProvider(
        results=[
            result("a", "https://a.example"),
            result("b", "https://b.example"),
            result("c", "https://c.example"),
        ]
    )
    service = SearchService(make_settings(search_max_results=2), provider=provider)

    outcome = await service.search("q", mode="auto")

    assert len(outcome.results) == 2


async def test_fallback_on_primary_error() -> None:
    primary = FakeProvider(name="primary", error=SearchTimeoutError("超时"))
    fallback = FakeProvider(name="fallback", results=[result("fallback-result")])
    service = SearchService(
        make_settings(), provider=FallbackSearchProvider(primary, fallback)
    )

    outcome = await service.search("q", mode="auto")

    assert outcome.status == "ok"
    assert outcome.provider == "fallback"
    assert outcome.results[0].title == "fallback-result"
    assert primary.calls == 1
    assert fallback.calls == 1


async def test_fallback_on_primary_empty() -> None:
    primary = FakeProvider(name="primary", results=[])
    fallback = FakeProvider(name="fallback", results=[result("from-fallback")])
    service = SearchService(
        make_settings(), provider=FallbackSearchProvider(primary, fallback)
    )

    outcome = await service.search("q", mode="auto")

    assert outcome.status == "ok"
    assert outcome.provider == "fallback"


async def test_fallback_skips_unconfigured_primary() -> None:
    primary = FakeProvider(name="primary", configured=False)
    fallback = FakeProvider(name="fallback", results=[result()])
    service = SearchService(
        make_settings(), provider=FallbackSearchProvider(primary, fallback)
    )

    outcome = await service.search("q", mode="auto")

    assert outcome.status == "ok"
    assert primary.calls == 0
    assert outcome.provider == "fallback"


async def test_fallback_both_empty_is_empty() -> None:
    primary = FakeProvider(name="primary", results=[])
    fallback = FakeProvider(name="fallback", results=[])
    service = SearchService(
        make_settings(), provider=FallbackSearchProvider(primary, fallback)
    )

    outcome = await service.search("q", mode="auto")

    assert outcome.status == "empty"


async def test_fallback_both_error_is_unavailable_with_reasons() -> None:
    primary = FakeProvider(name="primary", error=SearchAuthError("primary bad"))
    fallback = FakeProvider(
        name="fallback", error=SearchUpstreamError("fallback bad")
    )
    service = SearchService(
        make_settings(), provider=FallbackSearchProvider(primary, fallback)
    )

    outcome = await service.search("q", mode="auto")

    assert outcome.status == "unavailable"
    assert "primary bad" in (outcome.message or "")
    assert "fallback bad" in (outcome.message or "")


def test_build_search_provider_variants() -> None:
    tavily = build_search_provider(
        make_settings(search_provider="tavily", tavily_api_key="tvly-test")
    )
    assert isinstance(tavily, TavilyProvider)

    serpapi = build_search_provider(
        make_settings(search_provider="serpapi", serpapi_api_key="serp-test")
    )
    assert isinstance(serpapi, SerpApiProvider)

    fallback_only = build_search_provider(
        make_settings(
            search_provider="none",
            search_fallback_provider="serpapi",
            serpapi_api_key="serp-test",
        )
    )
    assert isinstance(fallback_only, SerpApiProvider)

    combined = build_search_provider(
        make_settings(
            search_provider="tavily",
            search_fallback_provider="serpapi",
            tavily_api_key="tvly-test",
            serpapi_api_key="serp-test",
        )
    )
    assert isinstance(combined, FallbackSearchProvider)

    same = build_search_provider(
        make_settings(
            search_provider="tavily",
            search_fallback_provider="tavily",
            tavily_api_key="tvly-test",
        )
    )
    assert isinstance(same, TavilyProvider)


async def test_service_overfetches_for_ranking() -> None:
    provider = FakeProvider(results=[result()])
    service = SearchService(make_settings(), provider=provider)

    outcome = await service.search("解释一下 RAG", mode="auto")

    assert provider.last_max_results == 10
    assert outcome.status == "ok"


async def test_broad_news_runs_subqueries() -> None:
    provider = FakeProvider(results=[result()])
    service = SearchService(make_settings(), provider=provider)

    outcome = await service.search("最近一周AI圈有什么新闻", mode="auto")

    assert outcome.status == "ok"
    assert provider.calls == 3  # 主查询 + 2 个子查询
    assert outcome.calls == 3


async def test_entity_query_does_not_run_subqueries() -> None:
    provider = FakeProvider(results=[result()])
    service = SearchService(make_settings(), provider=provider)

    await service.search("DeepSeek 最近发布了什么模型", mode="auto")

    assert provider.calls == 1


async def test_service_cache_hit_skips_provider() -> None:
    provider = FakeProvider(results=[result()])
    service = SearchService(make_settings(), provider=provider)

    first = await service.search("解释一下 RAG", mode="auto")
    second = await service.search("解释一下 RAG", mode="auto")

    assert first.cached is False
    assert second.cached is True
    assert provider.calls == 1


async def test_realtime_is_not_cached() -> None:
    provider = FakeProvider(results=[result()])
    service = SearchService(make_settings(), provider=provider)

    await service.search("英伟达最新股价", mode="auto")
    second = await service.search("英伟达最新股价", mode="auto")

    assert second.cached is False
    assert provider.calls == 2


class QueryAwareProvider(SearchProvider):
    name = "aware"

    def __init__(self, *, fail: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail = fail or set()

    @property
    def configured(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout_seconds: float,
        time_range: str | None = None,
        topic: str | None = None,
    ) -> list[SearchResult]:
        self.calls.append(query)
        if query in self.fail:
            raise SearchTimeoutError("子查询超时")
        return [result(title=f"结果-{query}", url=f"https://{len(self.calls)}.example")]


async def test_partial_subquery_failure_keeps_results() -> None:
    provider = QueryAwareProvider(fail={"大模型 最新发布"})
    service = SearchService(make_settings(), provider=provider)

    outcome = await service.search("最近一周AI圈有什么新闻", mode="auto")

    assert outcome.status == "ok"
    assert outcome.results
    assert provider.calls[0] == "最近一周AI圈有什么新闻"
    assert len(provider.calls) == 3


class SlowProvider(SearchProvider):
    name = "slow"

    @property
    def configured(self) -> bool:
        return True

    async def search(self, *args, **kwargs) -> list[SearchResult]:
        import asyncio

        await asyncio.sleep(0.5)
        return [result()]


async def test_total_deadline_degrades() -> None:
    settings = make_settings(search_total_timeout_seconds=0.05, search_timeout_seconds=1)
    service = SearchService(settings, provider=SlowProvider())

    outcome = await service.search("解释一下 RAG", mode="auto")

    assert outcome.status == "unavailable"
    assert "超时" in (outcome.message or "")


def test_search_available_reflects_keys() -> None:
    assert search_available(make_settings(search_provider="none")) is False
    assert (
        search_available(
            make_settings(search_provider="tavily", tavily_api_key="tvly-test")
        )
        is True
    )
    # 只配了 provider 但没 key，不算可用。
    assert search_available(make_settings(search_provider="tavily")) is False
