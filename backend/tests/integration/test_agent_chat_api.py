"""Agent 模式的聊天 API：工具事件、metadata、tool_runs 与 P1 回归。"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from tests.integration.conftest import create_conversation
from tests.integration.test_chat_stream_api import names, parse_sse, payload


async def stream_agent(
    client: AsyncClient, conversation_id: int, content: str
) -> tuple[list[tuple[str, dict]], dict]:
    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": content, "agent_mode": "auto"},
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    return events, payload(events, "message_start")


async def assistant_message(
    client: AsyncClient, conversation_id: int, message_id: int
) -> dict:
    messages = (
        await client.get(f"/api/v1/conversations/{conversation_id}/messages")
    ).json()
    return next(item for item in messages if item["id"] == message_id)


async def tool_runs(message_id: int) -> list[dict]:
    async with SessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT tool_name, status, error_code, duration_ms FROM tool_runs "
                "WHERE message_id = :mid ORDER BY id"
            ),
            {"mid": message_id},
        )
        return [dict(row._mapping) for row in result]


async def test_single_tool_emits_status_and_persists_run(client: AsyncClient) -> None:
    conversation = await create_conversation(client)
    events, start = await stream_agent(
        client,
        conversation["id"],
        '算一下 __mock_tool__calculator {"expression":"(1+2)*3"}',
    )
    order = names(events)

    assert order[0] == "message_start"
    assert order.index("tool_status") < order.index("token")
    assert order[-1] == "message_end"

    statuses = [data for name, data in events if name == "tool_status"]
    assert statuses[0]["status"] == "running"
    terminal = statuses[-1]
    assert terminal["tool"] == "calculator"
    assert terminal["status"] == "done"
    assert terminal["call_id"]
    assert "expression" in terminal["arguments_summary"]
    assert "9" in terminal["message"]

    assistant = await assistant_message(
        client, conversation["id"], start["assistant_message_id"]
    )
    metadata = assistant["metadata_json"]
    # steps 是 provider 调用次数（工具步 + 最终回答步），tool_calls 才是执行次数。
    assert metadata["agent"]["tool_calls"] == 1
    assert metadata["agent"]["limit_reached"] is False
    assert metadata["agent"]["steps"] >= 1
    assert len(metadata["tool_statuses"]) == 1

    rows = await tool_runs(start["assistant_message_id"])
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "calculator"
    assert rows[0]["status"] == "success"

    messages = (
        await client.get(f"/api/v1/conversations/{conversation['id']}/messages")
    ).json()
    user = next(item for item in messages if item["role"] == "user")
    assert user["metadata_json"]["agent_mode"] == "auto"


async def test_failure_is_fed_back_and_multi_step_runs(
    client: AsyncClient,
) -> None:
    conversation = await create_conversation(client)

    events, start = await stream_agent(
        client, conversation["id"], '__mock_tool__calculator {"wrong_field":"x"}'
    )
    terminal = [data for name, data in events if name == "tool_status"][-1]
    assert terminal["status"] == "failed"
    assert terminal["error_code"] == "invalid_arguments"
    assert "message_end" in names(events)
    rows = await tool_runs(start["assistant_message_id"])
    assert len(rows) == 1 and rows[0]["status"] == "failed"
    assert rows[0]["error_code"] == "invalid_arguments"

    events, start = await stream_agent(
        client,
        conversation["id"],
        '两行\n__mock_tool__calculator {"expression":"1+1"}\n'
        '__mock_tool__calculator {"expression":"2+2"}',
    )
    terminals = [
        data
        for name, data in events
        if name == "tool_status" and data["status"] == "done"
    ]
    assert [item["message"] for item in terminals] == ["计算结果：2", "计算结果：4"]

    assistant = await assistant_message(
        client, conversation["id"], start["assistant_message_id"]
    )
    assert assistant["metadata_json"]["agent"]["tool_calls"] == 2
    assert len(await tool_runs(start["assistant_message_id"])) == 2


async def test_agent_mode_off_keeps_p1_path(client: AsyncClient) -> None:
    conversation = await create_conversation(client)
    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/messages/stream",
        json={"content": '__mock_tool__calculator {"expression":"1+1"}'},
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)

    assert "tool_status" not in names(events)
    assert "source" not in names(events)
    assert "mock provider" in "".join(
        data.get("text", "") for name, data in events if name == "token"
    )

    assistant = payload(events, "message_start")
    rows = await tool_runs(assistant["assistant_message_id"])
    assert rows == []

    messages = (
        await client.get(f"/api/v1/conversations/{conversation['id']}/messages")
    ).json()
    user = next(item for item in messages if item["role"] == "user")
    assert user["metadata_json"]["agent_mode"] == "off"
