"""联网搜索服务：主源 + 可选 fallback、结果去重、失败降级。

聊天层只拿到 SearchOutcome，不关心底层是 Tavily 还是 SerpApi。搜索失败不会
抛到 HTTP 层——聊天继续，由提示词和 tool_status 明确告知用户。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.config import Settings
from app.search.base import (
    SearchError,
    SearchProvider,
    SearchResult,
    SearchTimeoutError,
    SearchUpstreamError,
)
from app.search.cache import TTLCache, cache_key
from app.search.dates import today_local
from app.search.query_planner import (
    QueryPlanner,
    RuleBasedPlanner,
    plan_query,
)
from app.search.ranking import domain_of, postprocess_results
from app.search.serpapi import SerpApiProvider
from app.search.tavily import TavilyProvider

logger = logging.getLogger(__name__)

SearchStatus = Literal["disabled", "unavailable", "empty", "ok"]

_WHITESPACE = re.compile(r"\s+")
_TRACKING_KEYS = {"gclid", "fbclid", "msclkid"}
MAX_QUERY_CHARS = 400



@dataclass(frozen=True, slots=True)
class SearchOutcome:
    status: SearchStatus
    results: tuple[SearchResult, ...] = ()
    message: str | None = None
    provider: str | None = None
    intent: str | None = None
    latency_ms: int = 0
    calls: int = 0
    cached: bool = False


def normalize_query(query: str) -> str:
    """压缩空白并截断；不要用 clean_text，避免破坏代码查询里的 <...>。"""
    return _WHITESPACE.sub(" ", query or "").strip()[:MAX_QUERY_CHARS]


def detect_time_range(query: str) -> str | None:
    """兼容旧调用：返回规则规划器识别出的 provider 时间范围。"""
    return plan_query(query).time_range


def detect_topic(query: str) -> str | None:
    """兼容旧调用：只有明确新闻语义才返回 news。"""
    return plan_query(query).topic


class FallbackSearchProvider(SearchProvider):
    """主源异常或空结果时才尝试备源；两家都没配置时整体视为未配置。"""

    def __init__(self, primary: SearchProvider, fallback: SearchProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+{fallback.name}"
        self.last_provider: str | None = None

    @property
    def configured(self) -> bool:
        return self.primary.configured or self.fallback.configured

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout_seconds: float,
        time_range: str | None = None,
        topic: str | None = None,
    ) -> list[SearchResult]:
        self.last_provider = None
        errors: list[str] = []
        any_success = False

        for provider in (self.primary, self.fallback):
            if not provider.configured:
                continue
            try:
                results = await provider.search(
                    query,
                    max_results=max_results,
                    timeout_seconds=timeout_seconds,
                    time_range=time_range,
                    topic=topic,
                )
            except SearchError as exc:
                errors.append(f"{provider.name}: {exc}")
                continue
            any_success = True
            self.last_provider = provider.name
            if results:
                return results

        if any_success:
            # 至少一家正常返回了空结果：这是 empty，不是 unavailable。
            return []
        raise SearchUpstreamError("；".join(errors) or "联网搜索不可用")


def _build_provider(name: str, settings: Settings) -> SearchProvider | None:
    if name == "tavily":
        return TavilyProvider(
            settings.tavily_api_key.get_secret_value(),
            snippet_chars=settings.search_snippet_chars,
            include_raw_content=settings.search_include_raw_content,
            content_chars=settings.search_content_chars,
        )
    if name == "serpapi":
        return SerpApiProvider(
            settings.serpapi_api_key.get_secret_value(),
            snippet_chars=settings.search_snippet_chars,
        )
    return None


def build_search_provider(settings: Settings) -> SearchProvider | None:
    primary = _build_provider(settings.search_provider, settings)
    fallback = (
        _build_provider(settings.search_fallback_provider, settings)
        if settings.search_fallback_provider
        else None
    )
    if primary is None:
        # 允许只配置备源：主源 none 时直接使用 fallback。
        return fallback
    if fallback is not None and fallback.name != primary.name:
        return FallbackSearchProvider(primary, fallback)
    return primary


def _canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except (ValueError, UnicodeError):
        return url
    if not hostname:
        return url

    host = hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    default_port = (parts.scheme == "http" and port == 80) or (
        parts.scheme == "https" and port == 443
    )
    netloc = host if not port or default_port else f"{host}:{port}"

    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in _TRACKING_KEYS
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def dedupe_results(results: list[SearchResult], limit: int) -> list[SearchResult]:
    seen: set[str] = set()
    deduped: list[SearchResult] = []
    for result in results:
        key = _canonical_url(result.url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
        if len(deduped) >= limit:
            break
    return deduped


class SearchService:
    def __init__(
        self,
        settings: Settings,
        provider: SearchProvider | None = None,
        *,
        planner: QueryPlanner | None = None,
        cache: TTLCache | None = None,
        resolve_provider: bool = True,
    ) -> None:
        self.settings = settings
        self._provider = provider
        # 显式传入 provider（包括 None）时不再自动解析，便于测试注入。
        self._resolved = not resolve_provider or provider is not None
        self.planner = planner or RuleBasedPlanner(
            max_subqueries=settings.search_max_subqueries
        )
        self.cache = cache or TTLCache(
            ttl_seconds=settings.search_cache_ttl_seconds,
            max_entries=settings.search_cache_max_entries,
        )

    @property
    def provider(self) -> SearchProvider | None:
        if not self._resolved:
            self._provider = build_search_provider(self.settings)
            self._resolved = True
        return self._provider

    def _provider_name(self, provider: SearchProvider) -> str:
        return getattr(provider, "last_provider", None) or provider.name

    def _extra_authority_domains(self) -> frozenset[str]:
        return frozenset(
            item.strip().lower()
            for item in self.settings.search_extra_authority_domains.split(",")
            if item.strip()
        )

    async def _fetch_query(
        self,
        provider: SearchProvider,
        query: str,
        plan,
        deadline: float,
    ) -> list[SearchResult]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SearchTimeoutError("联网搜索超时")
        timeout = min(self.settings.search_timeout_seconds, remaining)
        try:
            return await asyncio.wait_for(
                provider.search(
                    query,
                    max_results=self.settings.search_fetch_results,
                    timeout_seconds=timeout,
                    time_range=plan.time_range,
                    topic=plan.topic,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise SearchTimeoutError("联网搜索超时") from exc

    async def _fetch_plan(
        self,
        provider: SearchProvider,
        plan,
        deadline: float,
    ) -> tuple[list[SearchResult], int]:
        calls = 0
        primary: list[SearchResult] = []
        primary_error: SearchError | None = None
        try:
            primary = await self._fetch_query(provider, plan.search_query, plan, deadline)
        except SearchError as exc:
            primary_error = exc
        finally:
            calls += 1

        results = list(primary)
        domains = {domain_of(result.url) for result in primary}
        should_subquery = (
            self.settings.search_subqueries_enabled
            and plan.broad_news
            and bool(plan.subqueries)
            and (len(domains) < 3 or len(primary) < self.settings.search_max_results)
        )
        if should_subquery and deadline - time.monotonic() > 0.5:
            subqueries = plan.subqueries[: self.settings.search_max_subqueries]
            outcomes = await asyncio.gather(
                *(
                    self._fetch_query(provider, subquery, plan, deadline)
                    for subquery in subqueries
                ),
                return_exceptions=True,
            )
            calls += len(subqueries)
            for outcome in outcomes:
                if isinstance(outcome, list):
                    results.extend(outcome)
                elif isinstance(outcome, Exception):
                    logger.info("子查询失败：%s", outcome)

        if not results and primary_error is not None:
            raise primary_error
        return results, calls

    async def search(self, query: str, *, mode: str) -> SearchOutcome:
        started = time.monotonic()
        if mode != "auto":
            return SearchOutcome(status="disabled")

        query = normalize_query(query)
        if not query:
            return SearchOutcome(status="unavailable", message="搜索问题为空")

        try:
            plan = await self.planner.plan(query)
        except Exception:  # noqa: BLE001 - 规划失败回退规则
            logger.exception("查询规划失败，回退规则")
            plan = plan_query(query, max_subqueries=self.settings.search_max_subqueries)

        provider = self.provider
        if provider is None or not provider.configured:
            return SearchOutcome(
                status="unavailable",
                message="联网搜索未配置，请在 backend/.env 填写 TAVILY_API_KEY 或 SERPAPI_API_KEY",
                intent=plan.intent,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        key = cache_key(
            query, plan, provider.name, self.settings.search_max_results
        )
        cached = None if plan.is_realtime else self.cache.get(key)
        if cached is not None:
            return SearchOutcome(
                status="ok",
                results=tuple(cached),
                message=f"已通过缓存找到 {len(cached)} 条结果",
                provider=self._provider_name(provider),
                intent=plan.intent,
                latency_ms=int((time.monotonic() - started) * 1000),
                calls=0,
                cached=True,
            )

        deadline = started + self.settings.search_total_timeout_seconds
        try:
            raw_results, calls = await self._fetch_plan(provider, plan, deadline)
        except SearchError as exc:
            return SearchOutcome(
                status="unavailable",
                message=str(exc),
                provider=self._provider_name(provider),
                intent=plan.intent,
                latency_ms=int((time.monotonic() - started) * 1000),
                calls=1,
            )
        except Exception:  # noqa: BLE001 - 搜索永远不能穿透到聊天流
            logger.exception("联网搜索出现未预期异常")
            return SearchOutcome(
                status="unavailable",
                message="联网搜索暂时不可用",
                intent=plan.intent,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        processed = postprocess_results(
            raw_results,
            plan=plan,
            final_limit=self.settings.search_max_results,
            per_domain_cap=self.settings.search_per_domain_max_results,
            extra_authority_domains=self._extra_authority_domains(),
            now=today_local(),
        )
        used = self._provider_name(provider)
        latency_ms = int((time.monotonic() - started) * 1000)
        if not processed:
            return SearchOutcome(
                status="empty",
                message="联网搜索没有找到相关结果",
                provider=used,
                intent=plan.intent,
                latency_ms=latency_ms,
                calls=calls,
            )

        if not plan.is_realtime:
            self.cache.set(key, processed)
        suffix = f"（时间范围：{plan.time_range}）" if plan.time_range else ""
        if plan.is_realtime:
            suffix += "（实时数据可能有延迟，请以官方数据为准）"
        return SearchOutcome(
            status="ok",
            results=tuple(processed),
            message=f"已通过 {used} 找到 {len(processed)} 条结果{suffix}",
            provider=used,
            intent=plan.intent,
            latency_ms=latency_ms,
            calls=calls,
        )
