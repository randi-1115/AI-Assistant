"""进程内搜索结果缓存。

单用户本地应用不需要分布式缓存；一个 TTL + 容量上限的 dict 足够。
只缓存非空成功结果；key 包含 query、provider、时间窗、topic、子查询和
最终结果数，避免不同规划互相污染。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any

from app.search.base import SearchResult
from app.search.query_planner import QueryPlan


class TTLCache:
    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max(1, max_entries)
        self.clock = clock
        self._data: OrderedDict[str, tuple[float, list[SearchResult]]] = OrderedDict()

    def get(self, key: str) -> list[SearchResult] | None:
        if self.ttl_seconds <= 0:
            return None
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self.clock() >= expires_at:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return list(value)

    def set(self, key: str, value: Sequence[SearchResult]) -> None:
        if self.ttl_seconds <= 0 or not value:
            return
        self._data[key] = (self.clock() + self.ttl_seconds, list(value))
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()


def cache_key(
    query: str,
    plan: QueryPlan,
    provider_name: str,
    final_limit: int,
) -> str:
    payload: dict[str, Any] = {
        "query": query,
        "provider": provider_name,
        "intent": plan.intent,
        "time_range": plan.time_range,
        "topic": plan.topic,
        "subqueries": list(plan.subqueries),
        "final_limit": final_limit,
    }
    if plan.date_window is not None:
        payload["window"] = [
            plan.date_window.start.isoformat(),
            plan.date_window.end.isoformat(),
        ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
