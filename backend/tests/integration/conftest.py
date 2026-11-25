"""集成测试夹具：真实的 MySQL 测试库 + ASGI 客户端。

不用 SQLite：BIGINT UNSIGNED、JSON、ON DELETE CASCADE 在不同方言上行为不同，
用 SQLite 会得到「假绿灯」。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import app.models  # noqa: F401  注册所有模型
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.main import app
from app.models.attachment import Attachment
from app.models.message import Message
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.user_repo import UserRepository

pytestmark = pytest.mark.usefixtures("_prepare_schema")


@pytest.fixture(scope="session", autouse=True)
async def _prepare_schema() -> AsyncIterator[None]:
    """建表。整个 session 一次，表结构直接从 ORM 定义生成。"""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_tables() -> AsyncIterator[None]:
    """每个用例前清空数据，保证互不影响。"""
    async with SessionLocal() as session:
        for table in (
            "tool_runs",
            "document_chunks",
            "attachments",
            "messages",
            "conversations",
            "users",
        ):
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()
    yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


# ---------- 测试辅助 ----------


async def current_user_id() -> int:
    """当前登录身份。第一阶段是固定的开发用户，所以直接按配置里的邮箱取。"""
    async with SessionLocal() as session:
        user = await UserRepository(session).get_or_create(
            email=get_settings().dev_user_email,
            display_name=get_settings().dev_user_display_name,
        )
        await session.commit()
        return user.id


async def create_conversation(client: AsyncClient, title: str | None = None) -> dict:
    payload = {} if title is None else {"title": title}
    response = await client.post("/api/v1/conversations", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def create_other_users_conversation() -> int:
    """建一个属于**别人**的会话，用来验证越权访问会被挡住。"""
    async with SessionLocal() as session:
        user = await UserRepository(session).get_or_create(
            email="someone-else@local", display_name="另一个用户"
        )
        conversation = await ConversationRepository(session).create(user.id, "别人的会话")
        await session.commit()
        return conversation.id


async def upload_file(
    client: AsyncClient,
    *,
    name: str = "notes.txt",
    content: bytes = "文件上传测试内容。".encode(),
    conversation_id: int | None = None,
):
    data = {} if conversation_id is None else {"conversation_id": str(conversation_id)}
    return await client.post(
        "/api/v1/files/upload",
        files={"file": (name, content, "text/plain")},
        data=data,
    )


async def create_other_users_attachment(name: str = "别人的文件.txt") -> int:
    """直接插一条属于别人的附件记录，用来验证越权访问。"""
    async with SessionLocal() as session:
        user = await UserRepository(session).get_or_create(
            email="someone-else@local", display_name="另一个用户"
        )
        attachment = Attachment(
            user_id=user.id,
            conversation_id=None,
            original_name=name,
            storage_key=f"{user.id}/209901/other.txt",
            extension=".txt",
            size_bytes=1,
            parse_status="ready",
        )
        session.add(attachment)
        await session.commit()
        return attachment.id


async def attachment_row(file_id: int) -> Attachment | None:
    async with SessionLocal() as session:
        return await session.get(Attachment, file_id)


async def chunk_rows(attachment_id: int) -> list[dict]:
    async with SessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT chunk_index, section_title, content, char_count "
                "FROM document_chunks WHERE attachment_id = :aid ORDER BY chunk_index"
            ),
            {"aid": attachment_id},
        )
        return list(result.mappings())


async def load_message(message_id: int) -> Message:
    async with SessionLocal() as session:
        message = await session.get(Message, message_id)
        assert message is not None, f"消息 {message_id} 不存在"
        return message


async def conversation_messages(conversation_id: int) -> list[Message]:
    async with SessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT id, role, content, status FROM messages "
                "WHERE conversation_id = :cid ORDER BY id"
            ),
            {"cid": conversation_id},
        )
        return list(result.mappings())
