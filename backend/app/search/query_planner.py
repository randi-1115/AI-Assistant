"""搜索查询规划：意图、时间窗、主题与子查询。

规则优先，低置信度时用一次小模型兜底。规划只产出结构化 QueryPlan，
不直接调用搜索 provider；失败/超时一律回退规则，不阻断聊天。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Literal, Protocol

from app.ai.providers.base import ChatMessage, ChatOptions, LLMProvider
from app.search.dates import (
    DateWindow,
    provider_time_range_for,
    resolve_explicit_date_window,
    today_local,
    window_for_time_range,
)

logger = logging.getLogger(__name__)

SearchIntent = Literal["news", "entity_update", "evergreen", "realtime_data", "general"]

_WHITESPACE = re.compile(r"\s+")
_LATIN_WORD = re.compile(r"\b[A-Z][A-Za-z0-9.-]{1,}\b")
_AI_WORD = re.compile(r"\bai\b", re.IGNORECASE)

_REALTIME_TERMS = (
    "股价",
    "股票",
    "行情",
    "汇率",
    "天气",
    "比分",
    "赛果",
    "油价",
    "金价",
    "币价",
    "比特币",
    "实时",
    "最新价格",
    "stock price",
    "live price",
    "exchange rate",
    "weather",
)
_NEWS_TERMS = ("新闻", "资讯", "动态", "头条", "发生了什么", "news", "latest news")
_ENTITY_ACTIONS = (
    "发布",
    "推出",
    "更新",
    "上线",
    "融资",
    "收购",
    "回应",
    "宣布",
    "announce",
    "launch",
    "release",
    "update",
)
_ENTITY_NAMES = (
    "openai",
    "anthropic",
    "deepseek",
    "google",
    "microsoft",
    "nvidia",
    "meta",
    "apple",
    "amazon",
    "tesla",
    "英伟达",
    "微软",
    "谷歌",
    "苹果",
    "亚马逊",
    "特斯拉",
    "阿里",
    "腾讯",
    "字节",
    "百度",
    "华为",
    "小米",
    "月之暗面",
    "智谱",
    "通义",
)
_EVERGREEN_TERMS = (
    "区别",
    "是什么",
    "为什么",
    "怎么",
    "如何",
    "原理",
    "解释",
    "对比",
    "difference",
    "explain",
    "how to",
    " vs ",
)
_BROAD_TERMS = (
    "ai圈",
    "科技圈",
    "人工智能",
    "大模型",
    "行业",
    "领域",
    "市场",
    "国内",
    "国际",
    "科技",
)
_RECENCY_TERMS = ("最近", "近期", "最新", "这几天", "目前", "recent", "latest")
_ALLOWED_INTENTS = {
    "news",
    "entity_update",
    "evergreen",
    "realtime_data",
    "general",
}
_ALLOWED_TIME_RANGES = {"day", "week", "month", "year"}


@dataclass(frozen=True, slots=True)
class QueryPlan:
    original: str
    search_query: str
    subqueries: tuple[str, ...] = ()
    time_range: str | None = None
    date_window: DateWindow | None = None
    topic: str | None = None
    intent: SearchIntent = "general"
    is_realtime: bool = False
    freshness_required: bool = False
    broad_news: bool = False
    confidence: Literal["high", "low"] = "high"
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LlmPlanResult:
    intent: SearchIntent
    time_range: str | None
    is_realtime: bool
    subqueries: tuple[str, ...]
    reason: str


def _normalize(query: str) -> str:
    return _WHITESPACE.sub(" ", query or "").strip()


def _has_entity(query: str) -> bool:
    lower = query.casefold()
    return any(name in lower for name in _ENTITY_NAMES) or bool(_LATIN_WORD.search(query))


def detect_intent(query: str) -> SearchIntent:
    lower = query.casefold()
    if any(term in lower for term in _REALTIME_TERMS):
        return "realtime_data"
    if any(term in lower for term in _NEWS_TERMS):
        return "news"
    if any(term in lower for term in _ENTITY_ACTIONS) and _has_entity(query):
        return "entity_update"
    if any(term in lower for term in _EVERGREEN_TERMS):
        return "evergreen"
    return "general"


def is_broad_news(query: str, intent: SearchIntent) -> bool:
    if intent != "news":
        return False
    lower = query.casefold()
    has_broad = any(term in lower for term in _BROAD_TERMS) or bool(_AI_WORD.search(query))
    has_entity = _has_entity(query)
    return has_broad or not has_entity


def build_subqueries(query: str, intent: SearchIntent, limit: int) -> tuple[str, ...]:
    if intent != "news" or limit <= 0:
        return ()
    lower = query.casefold()
    if any(term in lower for term in ("ai", "人工智能", "大模型", "ai圈")):
        templates = ("大模型 最新发布", "AI 融资 政策", "AI 产品 应用")
    elif any(term in lower for term in ("科技", "技术")):
        templates = ("科技 公司 发布", "科技 行业 政策", "科技 融资")
    else:
        templates = (f"{query} 最新进展", f"{query} 行业分析")

    seen = {query.casefold()}
    selected: list[str] = []
    for item in templates:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= limit:
            break
    return tuple(selected)


def plan_query(
    query: str,
    *,
    now: date | None = None,
    max_subqueries: int = 2,
) -> QueryPlan:
    current = now or today_local()
    normalized = _normalize(query)
    window = resolve_explicit_date_window(normalized, now=current)
    intent = detect_intent(normalized)

    if intent == "realtime_data" and (window is None or window.kind != "explicit"):
        # “最新股价/今天天气”默认看今天；显式历史日期则尊重原窗口。
        window = DateWindow(current, current, "今天", "relative")
    elif window is None and any(
        term in normalized.casefold() for term in _RECENCY_TERMS
    ):
        if intent in ("news", "entity_update"):
            window = DateWindow(
                current - timedelta(days=29), current, "最近30天", "relative"
            )

    time_range = provider_time_range_for(window, now=current)
    topic = "news" if intent == "news" else None
    is_realtime = intent == "realtime_data"
    freshness_required = window is not None and intent != "evergreen"
    broad_news = is_broad_news(normalized, intent)
    subqueries = build_subqueries(normalized, intent, max_subqueries) if broad_news else ()

    confidence: Literal["high", "low"] = "high"
    reason = "rule"
    if intent == "general":
        confidence = "low"
        reason = "general intent"
    elif intent == "entity_update" and not _has_entity(normalized):
        confidence = "low"
        reason = "entity not identified"
    elif len(normalized) > 60 and intent in ("general", "entity_update"):
        confidence = "low"
        reason = "long/ambiguous query"

    return QueryPlan(
        original=query,
        search_query=normalized,
        subqueries=subqueries,
        time_range=time_range,
        date_window=window,
        topic=topic,
        intent=intent,
        is_realtime=is_realtime,
        freshness_required=freshness_required,
        broad_news=broad_news,
        confidence=confidence,
        reason=reason,
    )


class QueryPlanner(Protocol):
    async def plan(self, query: str) -> QueryPlan:
        ...


class RuleBasedPlanner:
    def __init__(self, *, max_subqueries: int = 2) -> None:
        self.max_subqueries = max_subqueries

    async def plan(self, query: str) -> QueryPlan:
        return plan_query(query, max_subqueries=self.max_subqueries)


_LLM_PROMPT = (
    "你是搜索规划器。只输出一个 JSON 对象，不要 Markdown、不要解释。字段："
    '{"intent":"news|entity_update|evergreen|realtime_data|general",'
    '"time_range":"day|week|month|year|null",'
    '"is_realtime":true|false,'
    '"subqueries":["..."]}。'
    "规则：只有明确新闻/资讯/动态才 intent=news；股价/汇率/天气/比分等 intent=realtime_data；"
    "实体发布/更新/融资等 intent=entity_update；解释/对比/原理 intent=evergreen；其余 general。"
    "subqueries 最多 2 条，只有宽泛新闻才需要拆分。"
)


class LlmQueryPlanner:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        timeout_seconds: float = 5.0,
        max_tokens: int = 256,
    ) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens

    async def plan(self, query: str) -> LlmPlanResult | None:
        try:
            result = await self.provider.chat(
                [
                    ChatMessage(role="system", content=_LLM_PROMPT),
                    ChatMessage(role="user", content=query),
                ],
                ChatOptions(
                    temperature=0.0,
                    max_output_tokens=self.max_tokens,
                    timeout_seconds=self.timeout_seconds,
                ),
            )
        except Exception:  # noqa: BLE001 - 规划失败必须回退规则
            logger.info("LLM 查询规划失败，回退规则")
            return None

        text = (result.text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None

        intent = payload.get("intent")
        if intent not in _ALLOWED_INTENTS:
            return None
        time_range = payload.get("time_range")
        if time_range not in _ALLOWED_TIME_RANGES:
            time_range = None
        is_realtime = bool(payload.get("is_realtime")) or intent == "realtime_data"
        raw_subqueries = payload.get("subqueries")
        subqueries: list[str] = []
        if isinstance(raw_subqueries, list):
            for item in raw_subqueries[:2]:
                if isinstance(item, str) and item.strip():
                    subqueries.append(item.strip())
        return LlmPlanResult(
            intent=intent,
            time_range=time_range,
            is_realtime=is_realtime,
            subqueries=tuple(subqueries),
            reason="llm",
        )


class HybridPlanner:
    """规则为主；只有低置信度才调用一次 LLM，失败回退规则。"""

    def __init__(
        self,
        rules: QueryPlanner,
        llm: LlmQueryPlanner | None,
        *,
        enabled: bool = True,
        max_subqueries: int = 2,
    ) -> None:
        self.rules = rules
        self.llm = llm
        self.enabled = enabled
        self.max_subqueries = max_subqueries

    async def plan(self, query: str) -> QueryPlan:
        base = await self.rules.plan(query)
        if not self.enabled or self.llm is None or base.confidence == "high":
            return base

        result = await self.llm.plan(query)
        if result is None:
            return base

        intent = result.intent
        time_range = result.time_range if result.time_range is not None else base.time_range
        date_window = base.date_window
        if result.time_range is not None and (
            date_window is None or date_window.kind != "explicit"
        ):
            date_window = window_for_time_range(result.time_range) or date_window
        elif date_window is None and time_range:
            date_window = window_for_time_range(time_range)
        if intent == "realtime_data" and (
            date_window is None or date_window.kind != "explicit"
        ):
            date_window = window_for_time_range("day")
            time_range = "day"
        topic = "news" if intent == "news" else None
        is_realtime = result.is_realtime or intent == "realtime_data"
        broad_news = is_broad_news(query, intent)
        subqueries = result.subqueries or (
            build_subqueries(query, intent, self.max_subqueries) if broad_news else ()
        )
        return replace(
            base,
            intent=intent,
            time_range=time_range,
            date_window=date_window,
            topic=topic,
            is_realtime=is_realtime,
            freshness_required=date_window is not None and intent != "evergreen",
            broad_news=broad_news,
            subqueries=subqueries,
            confidence="high",
            reason="llm",
        )
