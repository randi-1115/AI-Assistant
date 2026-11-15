"""搜索结果去重、日期过滤、权威性与多样性排序。"""

from __future__ import annotations

from datetime import date

from app.search.base import SearchResult
from app.search.query_planner import plan_query
from app.search.ranking import (
    authority_tier,
    canonical_url,
    dedupe_results,
    domain_of,
    is_listing_page,
    postprocess_results,
    title_key,
)

NOW = date(2026, 11, 15)


def result(
    title: str,
    url: str,
    *,
    published: str | None = "2026-11-12",
    snippet: str = "摘要",
) -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        snippet=snippet,
        published_date=published,
    )


def test_domain_and_canonical_url() -> None:
    assert domain_of("https://www.example.com/a?utm_source=x") == "example.com"
    assert canonical_url("https://www.example.com/a/?utm_source=x#frag") == "https://example.com/a"
    assert title_key("  OpenAI  发布：GPT-6！ ") == "openai发布gpt6"


def test_authority_tiers() -> None:
    assert authority_tier("openai.com") == 1
    assert authority_tier("blog.openai.com") == 1
    assert authority_tier("36kr.com") == 2
    assert authority_tier("zhihu.com") == 4
    assert authority_tier("example.com") == 2
    assert authority_tier("foo.ai", extra_domains=frozenset({"foo.ai"})) == 1


def test_listing_page_detection() -> None:
    assert is_listing_page("AI 新闻", "https://example.com/news/", "x") is True
    assert is_listing_page("首页", "https://example.com/", "x") is True
    assert is_listing_page("OpenAI 发布新模型", "https://example.com/article/1", "正文") is False


def test_dedupe_by_url_and_title_domain() -> None:
    results = [
        result("OpenAI 发布 A", "https://example.com/a?utm_source=x"),
        result("OpenAI 发布 A", "https://example.com/a"),
        result("OpenAI 发布 A", "https://other.com/a"),
    ]

    deduped = dedupe_results(results)

    assert len(deduped) == 2
    assert {domain_of(item.url) for item in deduped} == {"example.com", "other.com"}


def test_postprocess_prefers_authority_and_diversity() -> None:
    plan = plan_query("最近一周AI圈有什么新闻", now=NOW)
    results = [
        result("OpenAI 发布 A", "https://openai.com/news/a"),
        result("OpenAI 发布 A", "https://www.openai.com/news/a?utm_source=x"),
        result("Reuters 报道", "https://reuters.com/a"),
        result("36Kr 分析", "https://36kr.com/a"),
        result("知乎讨论", "https://zhihu.com/a"),
        result("AI 新闻列表", "https://example.com/news/"),
        result("旧闻", "https://example.com/old", published="2026-01-01"),
        result("无日期资料", "https://example.com/unknown", published=None),
    ]

    processed = postprocess_results(
        results,
        plan=plan,
        final_limit=5,
        per_domain_cap=2,
        now=NOW,
    )

    assert len(processed) == 5
    assert len({item.domain for item in processed}) >= 3
    assert all(item.title != "旧闻" for item in processed)
    assert sum(1 for item in processed if is_listing_page(item.title, item.url, item.snippet)) <= 1
    assert processed[0].domain in {"openai.com", "reuters.com"}
    assert all(item.score > 0 for item in processed)


def test_relevance_downranks_offtopic_result() -> None:
    plan = plan_query("最近一周AI圈有什么新闻", now=NOW)
    macro = SearchResult(
        title="2027年宏观展望",
        url="https://caixin.com/macro",
        snippet="出口与政策",
        published_date="2026-11-12",
        provider_score=0.3,
    )
    ai = SearchResult(
        title="AI 新模型发布",
        url="https://example.com/ai",
        snippet="大模型发布",
        published_date="2026-11-12",
        provider_score=0.8,
    )

    processed = postprocess_results(
        [macro, ai], plan=plan, final_limit=2, per_domain_cap=2, now=NOW
    )

    assert processed[0].title == "AI 新模型发布"


def test_entity_update_prefers_release_over_ban_news() -> None:
    plan = plan_query("DeepSeek 最近发布了什么模型", now=NOW)
    ban = SearchResult(
        title="美国禁用 DeepSeek",
        url="https://bbc.com/x",
        snippet="DeepSeek 模型受到限制",
        published_date=None,
        provider_score=0.7,
    )
    release = SearchResult(
        title="DeepSeek 全新模型发布",
        url="https://tencent.com/x",
        snippet="DeepSeek 发布新模型",
        published_date=None,
        provider_score=0.5,
    )

    processed = postprocess_results(
        [ban, release], plan=plan, final_limit=2, per_domain_cap=2, now=NOW
    )

    assert processed[0].title == "DeepSeek 全新模型发布"


def test_postprocess_keeps_unknown_dates_and_caps_domains() -> None:
    plan = plan_query("最近一周AI圈有什么新闻", now=NOW)
    results = [
        result("Alpha 文章", "https://a.example/1", published=None),
        result("Beta 文章", "https://a.example/2", published=None),
        result("Gamma 文章", "https://a.example/3", published=None),
        result("Delta 文章", "https://b.example/1", published=None),
        result("Epsilon 文章", "https://c.example/1", published=None),
    ]

    processed = postprocess_results(
        results,
        plan=plan,
        final_limit=3,
        per_domain_cap=2,
        now=NOW,
    )

    assert len(processed) == 3
    assert sum(1 for item in processed if item.domain == "a.example") <= 2
    assert {"a.example", "b.example"} <= {item.domain for item in processed}
    assert all(item.published_on is None for item in processed)
