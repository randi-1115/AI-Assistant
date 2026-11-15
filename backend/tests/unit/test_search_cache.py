"""搜索缓存：TTL、容量与 key 组成。"""

from __future__ import annotations

from datetime import date

from app.search.base import SearchResult
from app.search.cache import TTLCache, cache_key
from app.search.query_planner import plan_query


def item(title: str = "t", url: str = "https://example.com") -> SearchResult:
    return SearchResult(title=title, url=url, snippet="s")


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_ttl_expiry_and_move_to_end() -> None:
    clock = FakeClock()
    cache = TTLCache(ttl_seconds=10, max_entries=2, clock=clock)

    cache.set("a", [item("a")])
    cache.set("b", [item("b")])
    assert cache.get("a") is not None

    clock.value = 10
    assert cache.get("a") is None
    assert cache.get("b") is None


def test_capacity_evicts_oldest() -> None:
    clock = FakeClock()
    cache = TTLCache(ttl_seconds=100, max_entries=2, clock=clock)

    cache.set("a", [item("a")])
    cache.set("b", [item("b")])
    cache.set("c", [item("c")])

    assert cache.get("a") is None
    assert cache.get("b") is not None
    assert cache.get("c") is not None


def test_does_not_cache_empty_and_zero_ttl() -> None:
    cache = TTLCache(ttl_seconds=10, max_entries=2)
    cache.set("empty", [])
    assert cache.get("empty") is None

    disabled = TTLCache(ttl_seconds=0, max_entries=2)
    disabled.set("a", [item()])
    assert disabled.get("a") is None


def test_cache_key_sensitive_to_plan_and_limit() -> None:
    now = date(2026, 11, 15)
    plan = plan_query("最近一周AI圈有什么新闻", now=now)
    base = cache_key("q", plan, "tavily", 5)

    assert base == cache_key("q", plan, "tavily", 5)
    assert base != cache_key("q2", plan, "tavily", 5)
    assert base != cache_key("q", plan, "serpapi", 5)
    assert base != cache_key("q", plan, "tavily", 6)
