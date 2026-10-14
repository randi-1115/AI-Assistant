"""Mock provider 的三项能力：固定回答、流式模拟、可控错误。

全程不联网——mock.py 根本没 import httpx。
"""

from __future__ import annotations

import pytest

from app.ai.providers.base import Capability, ChatMessage
from app.ai.providers.mock import (
    MID_STREAM_TOKENS_BEFORE_ERROR,
    TRIGGER_AUTH,
    TRIGGER_MID_STREAM,
    TRIGGER_RATE_LIMIT,
    TRIGGER_TIMEOUT,
    TRIGGER_UPSTREAM,
    MockProvider,
)
from app.core.errors import (
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)


def user(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=text)


def provider(**kwargs) -> MockProvider:
    # 关掉延时，测试不用真的等
    kwargs.setdefault("stream_delay_ms", 0)
    return MockProvider(**kwargs)


def test_capabilities():
    instance = provider()
    assert instance.supports(Capability.TEXT_CHAT)
    assert instance.supports(Capability.STREAMING)
    assert not instance.supports(Capability.VISION)
    assert not instance.supports(Capability.TOOL_CALLING)


async def test_chat_returns_template_with_message_count():
    result = await provider(reply_template="共 {message_count} 条").chat(
        [ChatMessage(role="system", content="s"), user("hi")]
    )
    assert result.text == "共 2 条"
    assert result.finish_reason == "stop"
    # mock 没有真实用量，必须是 None 而不是 0
    assert result.usage is None


async def test_custom_template_with_unknown_placeholder_does_not_crash():
    result = await provider(reply_template="带 {unknown} 占位符").chat([user("hi")])
    assert result.text == "带 {unknown} 占位符"


async def test_stream_tokens_concatenate_to_the_full_reply():
    instance = provider(reply_template="这是一段用于测试的分段文本", chunk_size=3)
    events = [event async for event in instance.stream_chat([user("hi")])]

    tokens = [event.text for event in events if event.type == "token"]
    assert len(tokens) > 1, "chunk_size=3 应该切出多片"
    assert "".join(tokens) == "这是一段用于测试的分段文本"
    assert events[-1].type == "done"
    assert events[-1].finish_reason == "stop"


async def test_stream_never_reports_fake_usage():
    events = [event async for event in provider().stream_chat([user("hi")])]
    assert not any(event.type == "usage" for event in events)


@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        (TRIGGER_UPSTREAM, LLMUpstreamError),
        (TRIGGER_TIMEOUT, LLMTimeoutError),
        (TRIGGER_RATE_LIMIT, LLMRateLimitError),
        (TRIGGER_AUTH, LLMAuthError),
    ],
)
async def test_triggers_raise_the_matching_error(trigger, expected):
    instance = provider()
    with pytest.raises(expected):
        await instance.chat([user(f"请回答 {trigger}")])
    with pytest.raises(expected):
        [event async for event in instance.stream_chat([user(f"请回答 {trigger}")])]


async def test_mid_stream_trigger_emits_some_tokens_then_fails():
    instance = provider(reply_template="abcdefghij", chunk_size=1)
    received: list[str] = []
    with pytest.raises(LLMUpstreamError):
        async for event in instance.stream_chat([user(TRIGGER_MID_STREAM)]):
            if event.type == "token" and event.text:
                received.append(event.text)

    # 已经吐出去的内容要保留，前端才能显示「出了一半然后失败」
    assert len(received) == MID_STREAM_TOKENS_BEFORE_ERROR


async def test_force_error_applies_to_every_call():
    instance = provider(force_error="rate_limit")
    with pytest.raises(LLMRateLimitError):
        await instance.chat([user("完全正常的提问")])


async def test_invalid_force_error_value_is_reported():
    instance = provider(force_error="没这个值")
    with pytest.raises(LLMUpstreamError, match="取值非法"):
        await instance.chat([user("hi")])


async def test_trigger_only_looks_at_the_last_user_message():
    # 历史里出现触发标记不影响，只看最后一条用户消息
    instance = provider()
    result = await instance.chat([user(TRIGGER_UPSTREAM), ChatMessage(role="assistant", content="ok"), user("正常提问")])
    assert "mock provider" in result.text
