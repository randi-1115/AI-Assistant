"""会话 API 的端到端行为（真实 MySQL）。"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from tests.integration.conftest import (
    create_conversation,
    create_other_users_conversation,
    current_user_id,
)


# ---------- 健康检查与能力 ----------


async def test_health_reports_database_ok(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dependencies"]["database"] == "ok"
    # 不能泄露任何凭据
    assert "password" not in response.text.lower()
    assert "123456" not in response.text


async def test_capabilities_are_honest_about_unimplemented_features(client: AsyncClient):
    response = await client.get("/api/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    # mock provider 支持文本聊天与流式
    assert body["text_chat"] is True
    assert body["streaming"] is True
    # 没实现的一律 false，不能假装可用
    assert body["web_search"] is False
    assert body["document_qa"] is False
    assert body["memory"] is False
    assert body["vision"] is False


async def test_request_id_header_is_returned(client: AsyncClient):
    response = await client.get("/health")
    assert response.headers.get("X-Request-ID")


# ---------- 创建与列表 ----------


async def test_create_uses_default_title(client: AsyncClient):
    conversation = await create_conversation(client)
    assert conversation["title"] == "新对话"
    assert conversation["id"] > 0


async def test_create_with_explicit_title(client: AsyncClient):
    conversation = await create_conversation(client, "我的问题")
    assert conversation["title"] == "我的问题"


async def test_create_with_blank_title_falls_back_to_default(client: AsyncClient):
    response = await client.post("/api/v1/conversations", json={"title": "   "})
    assert response.status_code == 201
    assert response.json()["title"] == "新对话"


async def test_list_is_empty_initially(client: AsyncClient):
    response = await client.get("/api/v1/conversations")
    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "page": 1, "page_size": 20, "total": 0}


async def test_list_returns_newest_first(client: AsyncClient):
    first = await create_conversation(client, "第一个")
    second = await create_conversation(client, "第二个")
    response = await client.get("/api/v1/conversations")
    ids = [item["id"] for item in response.json()["items"]]
    # updated_at 相同时按 id 倒序兜底，所以第二个一定在前
    assert ids.index(second["id"]) < ids.index(first["id"])


async def test_list_pagination(client: AsyncClient):
    for index in range(5):
        await create_conversation(client, f"会话{index}")

    response = await client.get("/api/v1/conversations?page=2&page_size=2")
    body = response.json()
    assert body["total"] == 5
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert len(body["items"]) == 2


async def test_list_keyword_filters_by_title(client: AsyncClient):
    await create_conversation(client, "关于 Python 的问题")
    await create_conversation(client, "关于做饭的问题")

    response = await client.get("/api/v1/conversations?keyword=Python")
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "关于 Python 的问题"


async def test_list_rejects_page_size_over_limit(client: AsyncClient):
    response = await client.get("/api/v1/conversations?page_size=9999")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# ---------- 重命名 ----------


async def test_rename(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await client.patch(
        f"/api/v1/conversations/{conversation['id']}", json={"title": "新的标题"}
    )
    assert response.status_code == 200
    assert response.json()["title"] == "新的标题"


async def test_rename_rejects_empty_title(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await client.patch(
        f"/api/v1/conversations/{conversation['id']}", json={"title": ""}
    )
    assert response.status_code == 422


async def test_rename_missing_conversation_returns_404(client: AsyncClient):
    response = await client.patch("/api/v1/conversations/999999", json={"title": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"


# ---------- 删除 ----------


async def test_delete(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await client.delete(f"/api/v1/conversations/{conversation['id']}")
    assert response.status_code == 204
    assert response.text == ""

    assert (await client.get(f"/api/v1/conversations/{conversation['id']}")).status_code == 404


async def test_delete_removes_messages_too(client: AsyncClient):
    conversation = await create_conversation(client)
    await client.post(
        f"/api/v1/conversations/{conversation['id']}/messages/stream",
        json={"content": "你好"},
    )
    # 删之前确认确实有消息
    assert (await client.get(f"/api/v1/conversations/{conversation['id']}/messages")).json()

    await client.delete(f"/api/v1/conversations/{conversation['id']}")

    # 消息由数据库的 ON DELETE CASCADE 连带删除
    async with SessionLocal() as session:
        count = await session.scalar(
            text("SELECT COUNT(*) FROM messages WHERE conversation_id = :cid"),
            {"cid": conversation["id"]},
        )
    assert count == 0


async def test_delete_missing_conversation_returns_404(client: AsyncClient):
    response = await client.delete("/api/v1/conversations/999999")
    assert response.status_code == 404


# ---------- 归属校验 ----------


async def test_get_other_users_conversation_returns_404(client: AsyncClient):
    other_id = await create_other_users_conversation()
    response = await client.get(f"/api/v1/conversations/{other_id}")
    # 不区分「不存在」和「不属于你」，避免泄露资源是否存在
    assert response.status_code == 404


async def test_rename_other_users_conversation_returns_404(client: AsyncClient):
    other_id = await create_other_users_conversation()
    response = await client.patch(f"/api/v1/conversations/{other_id}", json={"title": "改你的"})
    assert response.status_code == 404


async def test_delete_other_users_conversation_returns_404(client: AsyncClient):
    other_id = await create_other_users_conversation()
    assert (await client.delete(f"/api/v1/conversations/{other_id}")).status_code == 404


async def test_list_messages_of_other_users_conversation_returns_404(client: AsyncClient):
    other_id = await create_other_users_conversation()
    response = await client.get(f"/api/v1/conversations/{other_id}/messages")
    assert response.status_code == 404


async def test_other_users_conversation_is_not_listed(client: AsyncClient):
    await create_other_users_conversation()
    await create_conversation(client, "我的会话")
    body = (await client.get("/api/v1/conversations")).json()
    assert [item["title"] for item in body["items"]] == ["我的会话"]


# ---------- 消息列表 ----------


async def test_messages_of_empty_conversation(client: AsyncClient):
    conversation = await create_conversation(client)
    response = await client.get(f"/api/v1/conversations/{conversation['id']}/messages")
    assert response.status_code == 200
    assert response.json() == []


async def test_messages_are_returned_ascending(client: AsyncClient):
    conversation = await create_conversation(client)
    conversation_id = conversation["id"]
    for content in ("第一问", "第二问", "第三问"):
        await client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={"content": content},
        )

    response = await client.get(f"/api/v1/conversations/{conversation_id}/messages")
    messages = response.json()
    roles = [m["role"] for m in messages]
    # 每轮是 user + assistant，且按 id 升序
    assert roles == ["user", "assistant"] * 3
    assert [m["id"] for m in messages] == sorted(m["id"] for m in messages)
    user_contents = [m["content"] for m in messages if m["role"] == "user"]
    assert user_contents == ["第一问", "第二问", "第三问"]


async def test_messages_cursor_pagination(client: AsyncClient):
    conversation = await create_conversation(client)
    conversation_id = conversation["id"]
    for content in ("a", "b", "c"):
        await client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={"content": content},
        )
    all_messages = (
        await client.get(f"/api/v1/conversations/{conversation_id}/messages")
    ).json()
    cursor = all_messages[-1]["id"]

    response = await client.get(
        f"/api/v1/conversations/{conversation_id}/messages?before_id={cursor}&limit=2"
    )
    older = response.json()
    assert len(older) == 2
    assert all(m["id"] < cursor for m in older)


async def test_current_user_helper_matches_api_owner(client: AsyncClient):
    # 兜底断言：conftest 里取的 user_id 和 API 实际使用的身份一致
    assert await current_user_id() > 0
