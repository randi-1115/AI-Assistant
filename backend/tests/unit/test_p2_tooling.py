"""P2 关键链路：provider tool calls 与 agent 纯函数。

只覆盖最容易回归的契约：
- OpenAI 流式 tool_call 分片累积、非流式解析、请求体序列化；
- 来源去重/全局编号、引用重写、结构化输出的 JSON 解析。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.ai.providers.base import ChatMessage, ChatOptions, ToolCall
from app.ai.providers.openai_compat import OpenAICompatibleProvider
from app.services.agent_service import (
    TurnState,
    _merge_sources,
    _parse_structured_action,
    _rewrite_citations,
)


def sse_body(chunks: list[Any]) -> bytes:
    lines = [
        f"data: {chunk if isinstance(chunk, str) else json.dumps(chunk, ensure_ascii=False)}"
        for chunk in chunks
    ]
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


def make_provider(handler) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="test",
        base_url="https://api.test",
        api_key="test-key",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )


async def test_tool_call_deltas_accumulate_and_non_stream_parses() -> None:
    fragments = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "calculator", "arguments": '{"expr'},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": 'ession":"1+2"}'}}
                        ]
                    }
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    ]

    async def stream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body(fragments))

    events = [
        event
        async for event in make_provider(stream_handler).stream_chat(
            [ChatMessage("user", "hi")], ChatOptions(tools=({"type": "function"},))
        )
    ]
    calls = [event.tool_call for event in events if event.type == "tool_call"]
    assert calls == [
        ToolCall(id="call_1", name="calculator", arguments='{"expression":"1+2"}')
    ]
    assert events[-1].type == "done"
    assert events[-1].finish_reason == "tool_calls"

    def chat_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_2",
                                    "function": {
                                        "name": "web_search",
                                        "arguments": '{"query":"x"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    result = await make_provider(chat_handler).chat([ChatMessage("user", "hi")])
    assert result.tool_calls == (
        ToolCall(id="call_2", name="web_search", arguments='{"query":"x"}'),
    )


async def test_payload_serializes_tool_messages_and_tools() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = make_provider(handler)
    call = ToolCall(id="call_1", name="calculator", arguments='{"expression":"1+2"}')
    messages = [
        ChatMessage("assistant", "", tool_calls=(call,)),
        ChatMessage("tool", "3", tool_call_id="call_1"),
    ]
    tools = ({"type": "function", "function": {"name": "calculator"}},)
    await provider.chat(messages, ChatOptions(tools=tools, tool_choice="auto"))
    await provider.chat(messages)

    first, second = seen
    assert first["tools"] == list(tools)
    assert first["tool_choice"] == "auto"
    assert first["messages"][0]["content"] is None
    assert first["messages"][0]["tool_calls"][0]["function"]["arguments"] == (
        '{"expression":"1+2"}'
    )
    assert first["messages"][1]["tool_call_id"] == "call_1"
    assert "tools" not in second and "tool_choice" not in second


def test_source_merge_renumber_and_structured_action_parsing() -> None:
    state = TurnState()
    incoming = [
        {"kind": "file", "file_id": 1, "chunk_index": 0, "excerpt_number": 1},
        {"kind": "file", "file_id": 2, "chunk_index": 0, "excerpt_number": 2},
        {"kind": "file", "file_id": 1, "chunk_index": 0, "excerpt_number": 1},
        {"kind": "web", "url": "https://example.com/a", "title": "A"},
        {"kind": "web", "url": "https://example.com/a", "title": "A"},
    ]

    new_sources, mapping = _merge_sources(state, incoming)

    assert len(new_sources) == 3
    assert [item.get("excerpt_number") for item in new_sources[:2]] == [1, 2]
    assert mapping == {1: 1, 2: 2}
    assert _rewrite_citations("见 [资料 1] 和 [资料 2]", {1: 3, 2: 4}) == (
        "见 [资料 3] 和 [资料 4]"
    )

    assert _parse_structured_action(
        '```json\n{"action":"tool","tool":"calculator","arguments":{"expression":"1+2"}}\n```'
    ) == {"action": "tool", "tool": "calculator", "arguments": {"expression": "1+2"}}
    assert _parse_structured_action('{"action":"final","answer":"42"}') == {
        "action": "final",
        "answer": "42",
    }
    assert _parse_structured_action("普通回答") is None
    assert _parse_structured_action("{broken") is None
