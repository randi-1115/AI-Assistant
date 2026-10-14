"""OpenAI 兼容 adapter 的解析与错误映射。

用 httpx.MockTransport 喂假的 SSE 行，所以**不产生任何真实网络请求**。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.ai.providers.base import ChatMessage
from app.ai.providers.openai_compat import OpenAICompatibleProvider
from app.core.errors import (
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)


def sse_body(chunks: list[Any]) -> bytes:
    lines = []
    for chunk in chunks:
        text = chunk if isinstance(chunk, str) else json.dumps(chunk, ensure_ascii=False)
        lines.append(f"data: {text}")
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


def token_chunk(text: str, finish: str | None = None) -> dict:
    return {"choices": [{"delta": {"content": text}, "finish_reason": finish}]}


def make_provider(handler) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="test",
        base_url="https://api.test",
        api_key="test-key",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )


async def collect(provider: OpenAICompatibleProvider) -> list:
    return [event async for event in provider.stream_chat([ChatMessage("user", "hi")])]


# --- 正常解析 ---


async def test_stream_parses_tokens_done_and_openai_style_usage():
    """OpenAI 风格：最后一个 chunk 的 choices 为空，usage 单独挂在它上面。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_body(
                [
                    token_chunk("你"),
                    token_chunk("好"),
                    token_chunk("！", finish="stop"),
                    # choices 为空、只有 usage —— 这里如果不做守卫会 IndexError
                    {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
                    "[DONE]",
                ]
            ),
        )

    events = await collect(make_provider(handler))
    assert [e.text for e in events if e.type == "token"] == ["你", "好", "！"]
    usage_event = next(e for e in events if e.type == "usage")
    assert usage_event.usage.input_tokens == 7
    assert usage_event.usage.output_tokens == 3
    assert events[-1].type == "done"
    assert events[-1].finish_reason == "stop"


async def test_stream_parses_deepseek_style_usage_on_last_content_chunk():
    """DeepSeek 风格：不发单独的 usage chunk，用量挂在最后一个有内容的 chunk 上。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_body(
                [
                    token_chunk("答"),
                    {
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 4},
                    },
                    "[DONE]",
                ]
            ),
        )

    events = await collect(make_provider(handler))
    usage_event = next(e for e in events if e.type == "usage")
    assert usage_event.usage.input_tokens == 11
    assert events[-1].finish_reason == "stop"


async def test_stream_without_finish_reason_still_terminates():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body([token_chunk("a"), "[DONE]"]))

    events = await collect(make_provider(handler))
    assert events[-1].type == "done"
    assert events[-1].finish_reason == "stop"


async def test_keepalive_comments_and_blank_lines_are_ignored():
    def handler(request: httpx.Request) -> httpx.Response:
        body = b": ping\n\ndata: {\"choices\":[{\"delta\":{\"content\":\"x\"}}]}\n\n: ping\n\ndata: [DONE]\n\n"
        return httpx.Response(200, content=body)

    events = await collect(make_provider(handler))
    assert [e.text for e in events if e.type == "token"] == ["x"]


async def test_unparseable_data_line_is_skipped():
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            b"data: {not json}\n\n"
            b"data: {\"choices\":[{\"delta\":{\"content\":\"ok\"}}]}\n\n"
            b"data: [DONE]\n\n"
        )
        return httpx.Response(200, content=body)

    events = await collect(make_provider(handler))
    assert [e.text for e in events if e.type == "token"] == ["ok"]


async def test_missing_usage_yields_no_usage_event():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body([token_chunk("a", finish="stop"), "[DONE]"]))

    events = await collect(make_provider(handler))
    assert not any(e.type == "usage" for e in events)


# --- 请求体 ---


async def test_request_body_asks_for_usage_and_sets_stream():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=sse_body(["[DONE]"]))

    await collect(make_provider(handler))
    assert seen["stream"] is True
    assert seen["stream_options"] == {"include_usage": True}
    assert seen["model"] == "test-model"


async def test_authorization_header_is_sent():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=sse_body(["[DONE]"]))

    await collect(make_provider(handler))
    assert seen["auth"] == "Bearer test-key"


# --- 错误映射 ---


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, LLMAuthError), (403, LLMAuthError), (429, LLMRateLimitError), (500, LLMUpstreamError), (503, LLMUpstreamError)],
)
async def test_http_error_status_is_mapped(status, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "upstream detail"}})

    with pytest.raises(expected):
        await collect(make_provider(handler))


async def test_upstream_body_is_not_leaked_into_the_error_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal trace with secret-token-abc")

    with pytest.raises(LLMUpstreamError) as excinfo:
        await collect(make_provider(handler))
    assert "secret-token-abc" not in str(excinfo.value)
    # 也不该带上 API Key
    assert "test-key" not in str(excinfo.value)


async def test_timeout_is_mapped():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    with pytest.raises(LLMTimeoutError):
        await collect(make_provider(handler))


async def test_connection_error_is_mapped():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMUpstreamError):
        await collect(make_provider(handler))


# --- 非流式 ---


async def test_chat_parses_message_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": "完整回答"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5},
            },
        )

    result = await make_provider(handler).chat([ChatMessage("user", "hi")])
    assert result.text == "完整回答"
    assert result.finish_reason == "stop"
    assert result.usage.output_tokens == 5


async def test_chat_with_empty_choices_raises_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(LLMUpstreamError, match="空结果"):
        await make_provider(handler).chat([ChatMessage("user", "hi")])


# --- list_models（用于确认 LLM_MODEL 该填什么）---


async def test_list_models_returns_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/models"
        return httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-b"}]})

    assert await make_provider(handler).list_models() == ["model-a", "model-b"]


# --- 构造校验 ---


def test_missing_api_key_or_model_is_rejected():
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(name="x", base_url="https://a.test", api_key="", model="m")
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(name="x", base_url="https://a.test", api_key="k", model="")
