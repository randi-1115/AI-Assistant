"""查询规划：意图、时间窗、子查询与 LLM 兜底。"""

from __future__ import annotations

from datetime import date

import pytest

from app.ai.providers.base import (
    ChatMessage,
    ChatOptions,
    ChatResult,
    LLMProvider,
    StreamEvent,
)
from app.search.query_planner import (
    HybridPlanner,
    LlmQueryPlanner,
    RuleBasedPlanner,
    build_subqueries,
    detect_intent,
    plan_query,
)

NOW = date(2026, 11, 15)


def test_intents() -> None:
    assert detect_intent("最近一周AI圈有什么新闻") == "news"
    assert detect_intent("DeepSeek 最近发布了什么模型") == "entity_update"
    assert detect_intent("英伟达最新股价") == "realtime_data"
    assert detect_intent("Transformer 和 Mamba 的区别") == "evergreen"
    assert detect_intent("最近有什么值得关注的") == "general"


def test_broad_news_plan_and_subqueries() -> None:
    plan = plan_query("最近一周AI圈有什么新闻", now=NOW)

    assert plan.intent == "news"
    assert plan.time_range == "week"
    assert plan.topic == "news"
    assert plan.broad_news is True
    assert plan.is_realtime is False
    assert plan.confidence == "high"
    assert len(plan.subqueries) == 2


def test_entity_update_does_not_force_news_topic() -> None:
    plan = plan_query("DeepSeek 最近发布了什么模型", now=NOW)

    assert plan.intent == "entity_update"
    assert plan.time_range == "month"
    assert plan.topic is None
    assert plan.freshness_required is True


def test_realtime_uses_day_window() -> None:
    plan = plan_query("英伟达最新股价", now=NOW)

    assert plan.intent == "realtime_data"
    assert plan.time_range == "day"
    assert plan.topic is None
    assert plan.is_realtime is True


def test_explicit_date_window() -> None:
    plan = plan_query("2026年11月12日 AI 新闻", now=NOW)

    assert plan.intent == "news"
    assert plan.date_window is not None
    assert plan.date_window.start == date(2026, 11, 12)
    assert plan.time_range is None
    assert plan.topic == "news"


def test_evergreen_has_no_time_window() -> None:
    plan = plan_query("Transformer 和 Mamba 的区别", now=NOW)

    assert plan.intent == "evergreen"
    assert plan.time_range is None
    assert plan.date_window is None
    assert plan.topic is None
    assert plan.freshness_required is False


def test_general_query_is_low_confidence() -> None:
    plan = plan_query("最近有什么值得关注的", now=NOW)

    assert plan.intent == "general"
    assert plan.confidence == "low"


def test_entity_news_is_not_broad() -> None:
    plan = plan_query("OpenAI 最近新闻", now=NOW)

    assert plan.intent == "news"
    assert plan.topic == "news"
    assert plan.broad_news is False
    assert plan.subqueries == ()


def test_subquery_limit() -> None:
    assert build_subqueries("最近一周AI圈有什么新闻", "news", 0) == ()
    assert len(build_subqueries("最近一周AI圈有什么新闻", "news", 3)) == 3
    assert build_subqueries("最近一周AI圈有什么新闻", "entity_update", 2) == ()


class FakeLLM(LLMProvider):
    name = "fake"

    def __init__(self, text: str | None = None, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls = 0

    async def chat(
        self, messages: list[ChatMessage], options: ChatOptions | None = None
    ) -> ChatResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ChatResult(text=self.text or "")

    def stream_chat(self, messages, options=None):
        async def _gen():
            if False:
                yield StreamEvent.token("")

        return _gen()


async def test_llm_planner_parses_json() -> None:
    llm = FakeLLM(
        '```json\n{"intent":"news","time_range":"week","is_realtime":false,'
        '"subqueries":["大模型 最新发布"]}\n```'
    )
    planner = LlmQueryPlanner(llm)

    result = await planner.plan("最近有什么值得关注的")

    assert result is not None
    assert result.intent == "news"
    assert result.time_range == "week"
    assert result.subqueries == ("大模型 最新发布",)


async def test_llm_planner_rejects_bad_json_and_errors() -> None:
    assert await LlmQueryPlanner(FakeLLM("not json")).plan("q") is None
    assert (
        await LlmQueryPlanner(FakeLLM(error=RuntimeError("boom"))).plan("q") is None
    )


async def test_hybrid_skips_llm_for_high_confidence() -> None:
    llm = FakeLLM('{"intent":"news","time_range":"week","is_realtime":false,"subqueries":[]}')
    planner = HybridPlanner(RuleBasedPlanner(), LlmQueryPlanner(llm))

    plan = await planner.plan("最近一周AI圈有什么新闻")

    assert llm.calls == 0
    assert plan.intent == "news"


async def test_hybrid_uses_llm_for_low_confidence() -> None:
    llm = FakeLLM(
        '{"intent":"news","time_range":"week","is_realtime":false,'
        '"subqueries":["大模型 最新发布"]}'
    )
    planner = HybridPlanner(RuleBasedPlanner(), LlmQueryPlanner(llm))

    plan = await planner.plan("最近有什么值得关注的")

    assert llm.calls == 1
    assert plan.intent == "news"
    assert plan.topic == "news"
    assert plan.time_range == "week"
    assert plan.subqueries == ("大模型 最新发布",)


async def test_hybrid_falls_back_when_llm_fails() -> None:
    planner = HybridPlanner(
        RuleBasedPlanner(),
        LlmQueryPlanner(FakeLLM(error=RuntimeError("boom"))),
    )

    plan = await planner.plan("最近有什么值得关注的")

    assert plan.intent == "general"
    assert plan.confidence == "low"
