"""联网搜索（Tavily 主源 + SerpApi 可选备源）。"""

from fastapi import Depends

from app.ai.providers.base import LLMProvider
from app.ai.providers.registry import llm_provider_dep
from app.core.config import Settings, get_settings
from app.search.base import (
    SearchAuthError,
    SearchError,
    SearchNotConfigured,
    SearchProvider,
    SearchRateLimitError,
    SearchResult,
    SearchTimeoutError,
    SearchUpstreamError,
)
from app.search.cache import TTLCache
from app.search.pipeline import (
    build_web_block,
    build_web_empty_block,
    build_web_source_payloads,
    build_web_unavailable_block,
    merge_web_context,
)
from app.search.query_planner import (
    HybridPlanner,
    LlmQueryPlanner,
    QueryPlan,
    RuleBasedPlanner,
    plan_query,
)
from app.search.service import (
    SearchOutcome,
    SearchService,
    build_search_provider,
    dedupe_results,
    detect_time_range,
    detect_topic,
    normalize_query,
)

__all__ = [
    "HybridPlanner",
    "LlmQueryPlanner",
    "QueryPlan",
    "RuleBasedPlanner",
    "SearchAuthError",
    "SearchError",
    "SearchNotConfigured",
    "SearchOutcome",
    "SearchProvider",
    "SearchRateLimitError",
    "SearchResult",
    "SearchService",
    "SearchTimeoutError",
    "SearchUpstreamError",
    "build_search_provider",
    "build_web_block",
    "build_web_empty_block",
    "build_web_source_payloads",
    "build_web_unavailable_block",
    "dedupe_results",
    "detect_time_range",
    "detect_topic",
    "get_search_service",
    "merge_web_context",
    "normalize_query",
    "plan_query",
    "search_available",
]


def search_available(settings: Settings) -> bool:
    provider = build_search_provider(settings)
    return provider is not None and provider.configured


_SEARCH_CACHE: TTLCache | None = None


def get_search_service(
    provider: LLMProvider = Depends(llm_provider_dep),
) -> SearchService:
    """FastAPI 依赖。测试里用 app.dependency_overrides 注入假 provider。"""
    global _SEARCH_CACHE
    settings = get_settings()
    if _SEARCH_CACHE is None:
        _SEARCH_CACHE = TTLCache(
            ttl_seconds=settings.search_cache_ttl_seconds,
            max_entries=settings.search_cache_max_entries,
        )
    rules = RuleBasedPlanner(max_subqueries=settings.search_max_subqueries)
    planner = rules
    if settings.search_query_plan_enabled and settings.search_llm_planning_enabled:
        planner = HybridPlanner(
            rules,
            LlmQueryPlanner(
                provider,
                timeout_seconds=settings.search_planner_timeout_seconds,
                max_tokens=settings.search_planner_max_tokens,
            ),
            enabled=True,
            max_subqueries=settings.search_max_subqueries,
        )
    return SearchService(settings, planner=planner, cache=_SEARCH_CACHE)
