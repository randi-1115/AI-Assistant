"""流式聊天与重新生成的端到端行为（真实 MySQL + mock provider，不联网）。"""

from __future__ import annotations

import json

from httpx import AsyncClient

from app.ai.providers.mock import TRIGGER_MID_STREAM, TRIGGER_UPSTREAM, MockProvider
from app.services.chat_service import ChatService
from tests.integration.conftest import (
    create_conversation,
    create_other_users_conversation,
    current_user_id,
    load_message,
)


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        name = "message"
        data: dict = {}
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:") :].strip())
        events.append((name, data))
    return events


def names(events: list[tuple[str, dict]]) -> list[str]:
    return [name for name, _ in events]


def payload(events: list[tuple[str, dict]], wanted: str) -> dict:
    return next(data for name, data in events if name == wanted)


async def stream(client: AsyncClient, conversation_id: int, content: str):
    return await client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": content},
    )


async def messages_of(client: AsyncClient, conversation_id: int) -> list[dict]:
    response = await client.get(f"/api/v1/conversations/{conversation_id}/messages")
    assert response.status_code == 200
    return response.json()


# ---------- 正常流式 ----------


async def test_stream_emits_frames_in_order(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await stream(client, conversation["id"], "你好")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    order = names(events)
    assert order[0] == "message_start"
    assert order[-1] == "message_end"
    assert "token" in order
    # token 只能出现在 message_start 之后、message_end 之前
    assert order.index("message_start") < order.index("token") < order.index("message_end")


async def test_stream_start_carries_both_message_ids(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await stream(client, conversation["id"], "你好")
    events = parse_sse(response.text)

    start = payload(events, "message_start")
    assert start["user_message_id"] > 0
    assert start["assistant_message_id"] > 0
    assert start["user_message_id"] != start["assistant_message_id"]


async def test_tokens_concatenate_to_the_saved_content(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await stream(client, conversation["id"], "你好")
    events = parse_sse(response.text)

    streamed = "".join(data["text"] for name, data in events if name == "token")
    assert streamed, "应该有 token 事件"

    start = payload(events, "message_start")
    saved = await load_message(start["assistant_message_id"])
    assert saved.content == streamed
    assert saved.status == "complete"


async def test_stream_saves_user_message_before_assistant_reply(client: AsyncClient):
    conversation = await create_conversation(client)
    await stream(client, conversation["id"], "第一个问题")

    stored = await messages_of(client, conversation["id"])
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert stored[0]["content"] == "第一个问题"
    assert stored[0]["status"] == "complete"


async def test_multi_turn_keeps_context_growing(client: AsyncClient):
    conversation = await create_conversation(client)
    conversation_id = conversation["id"]
    for content in ("第一问", "第二问", "第三问"):
        await stream(client, conversation_id, content)

    stored = await messages_of(client, conversation_id)
    assert len(stored) == 6
    # mock 的回答里带上了本次请求的消息条数，能间接验证上下文确实在累积
    last_assistant = stored[-1]
    assert last_assistant["role"] == "assistant"
    assert "mock provider" in last_assistant["content"]


async def test_message_metadata_records_finish_reason(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await stream(client, conversation["id"], "你好")
    start = payload(parse_sse(response.text), "message_start")

    saved = await load_message(start["assistant_message_id"])
    assert saved.model_provider == "mock"
    assert saved.metadata_json is not None
    assert saved.metadata_json["finish_reason"] == "stop"
    # mock 没有真实用量，不能伪造
    assert "usage" not in saved.metadata_json


async def test_first_turn_auto_generates_title(client: AsyncClient):
    conversation = await create_conversation(client)
    assert conversation["title"] == "新对话"

    await stream(client, conversation["id"], "帮我看看 Python 装饰器怎么写")

    # 标题由后台任务生成，直接调一次生成逻辑，避免测试里等定时器。
    refreshed = (await client.get(f"/api/v1/conversations/{conversation['id']}")).json()
    assert refreshed["title"] != ""  # 至少不能被清空

    service = ChatService(MockProvider(stream_delay_ms=0))
    await service.generate_title(conversation["id"], "帮我看看 Python 装饰器怎么写")
    after = (await client.get(f"/api/v1/conversations/{conversation['id']}")).json()
    assert after["title"] != "新对话"


async def test_manual_title_is_not_overwritten_by_auto_title(client: AsyncClient):
    conversation = await create_conversation(client, "我自己起的名字")
    service = ChatService(MockProvider(stream_delay_ms=0))
    await service.generate_title(conversation["id"], "随便什么内容")
    after = (await client.get(f"/api/v1/conversations/{conversation['id']}")).json()
    assert after["title"] == "我自己起的名字"


# ---------- 错误路径 ----------


async def test_unknown_conversation_returns_json_error_not_sse(client: AsyncClient):
    """准备阶段的错误必须在开流之前抛出，走标准 JSON 错误。"""
    response = await client.post(
        "/api/v1/conversations/999999/messages/stream", json={"content": "你好"}
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "conversation_not_found"


async def test_stream_to_other_users_conversation_is_rejected(client: AsyncClient):
    other_id = await create_other_users_conversation()
    response = await client.post(
        f"/api/v1/conversations/{other_id}/messages/stream", json={"content": "你好"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"


async def test_empty_content_is_rejected(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await stream(client, conversation["id"], "")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_llm_error_becomes_an_error_event(client: AsyncClient):
    """流已经开始之后，错误只能靠 event: error 传递，状态码改不了。"""
    conversation = await create_conversation(client)
    response = await stream(client, conversation["id"], f"你好 {TRIGGER_UPSTREAM}")

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert names(events) == ["message_start", "error", "message_end"]

    error = payload(events, "error")
    assert error["code"] == "llm_upstream_error"
    assert error["message"]
    assert error["request_id"]
    # 上游细节不能外泄
    assert "HTTP" not in error["message"]

    end = payload(events, "message_end")
    assert end["finish_reason"] == "error"

    stored = await messages_of(client, conversation["id"])
    assert stored[-1]["status"] == "failed"


async def test_mid_stream_error_keeps_partial_content(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await stream(client, conversation["id"], f"你好 {TRIGGER_MID_STREAM}")
    events = parse_sse(response.text)

    partial = "".join(data["text"] for name, data in events if name == "token")
    assert partial, "中途失败前应该已经吐出了一部分内容"

    start = payload(events, "message_start")
    saved = await load_message(start["assistant_message_id"])
    assert saved.status == "failed"
    # 已经产出的内容必须保留，不能因为失败就清空
    assert saved.content == partial


# ---------- 停止生成 ----------


class FakeRequest:
    """模拟客户端断开：第 N 次被询问 is_disconnected 之后返回 True。"""

    def __init__(self, disconnect_after: int) -> None:
        self.calls = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > self.disconnect_after


async def test_client_disconnect_marks_message_interrupted(client: AsyncClient):
    conversation = await create_conversation(client)
    user_id = await current_user_id()
    service = ChatService(
        MockProvider(chunk_size=1, stream_delay_ms=0, reply_template="abcdefghij")
    )
    prepared = await service.prepare_send(
        conversation_id=conversation["id"], user_id=user_id, content="你好"
    )

    events = [event async for event in service.run_turn(prepared, FakeRequest(disconnect_after=3))]

    order = names(events)
    assert "message_start" in order
    assert "token" in order
    # 中断时不发 message_end——流是被掐断的
    assert "message_end" not in order

    saved = await load_message(prepared.assistant_message_id)
    assert saved.status == "interrupted"
    assert 0 < len(saved.content) < len("abcdefghij")


# ---------- 重新生成 ----------


async def test_regenerate_returns_a_fresh_stream(client: AsyncClient):
    conversation = await create_conversation(client)
    await stream(client, conversation["id"], "第一问")

    response = await client.post(f"/api/v1/conversations/{conversation['id']}/regenerate")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert names(parse_sse(response.text))[0] == "message_start"


async def test_regenerate_replaces_the_previous_answer(client: AsyncClient):
    conversation = await create_conversation(client)
    conversation_id = conversation["id"]
    await stream(client, conversation_id, "第一问")

    before = await messages_of(client, conversation_id)
    old_assistant_id = next(m["id"] for m in before if m["role"] == "assistant")

    await client.post(f"/api/v1/conversations/{conversation_id}/regenerate")

    after = await messages_of(client, conversation_id)
    # 仍然是「一问一答」，旧的回答被替换而不是并列保留
    assert [m["role"] for m in after] == ["user", "assistant"]
    assert after[1]["id"] != old_assistant_id
    assert after[0]["id"] == before[0]["id"]


async def test_regenerate_without_assistant_answer_is_rejected(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await client.post(f"/api/v1/conversations/{conversation['id']}/regenerate")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_regenerate_other_users_conversation_is_rejected(client: AsyncClient):
    other_id = await create_other_users_conversation()
    response = await client.post(f"/api/v1/conversations/{other_id}/regenerate")
    assert response.status_code == 404
