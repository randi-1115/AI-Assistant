"""文档检索工具的真实数据库验证：归属、会话绑定、就绪状态与截断。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.attachment import Attachment
from app.tools.base import ToolContext, ToolError, ToolUnavailable
from app.tools.document_search import DocumentSearchArgs, DocumentSearchTool
from tests.integration.conftest import (
    create_conversation,
    create_other_users_attachment,
    current_user_id,
    upload_file,
)

DOCUMENT_TEXT = (
    "项目代号是蓝鲸。\n\n"
    "部署窗口安排在周五晚上十点，负责人是张工。\n\n"
    "回滚方案是切回上一个稳定版本，数据不需要迁移。\n"
)


async def make_context(
    *, conversation_id: int | None, attachment_ids: tuple[int, ...]
) -> ToolContext:
    return ToolContext(
        user_id=await current_user_id(),
        conversation_id=conversation_id,
        attachment_ids=attachment_ids,
        session_factory=SessionLocal,
    )


async def upload_document(client: AsyncClient, conversation_id: int) -> int:
    uploaded = await upload_file(
        client, name="项目说明.txt", content=DOCUMENT_TEXT.encode(), conversation_id=conversation_id
    )
    assert uploaded.status_code == 201, uploaded.text
    payload = uploaded.json()
    assert payload["parse_status"] == "ready", payload
    return payload["file_id"]


async def test_owned_attachment_returns_excerpts_and_file_sources(
    client: AsyncClient,
) -> None:
    conversation = await create_conversation(client)
    file_id = await upload_document(client, conversation["id"])
    context = await make_context(
        conversation_id=conversation["id"], attachment_ids=(file_id,)
    )

    result = await DocumentSearchTool(get_settings()).run(
        DocumentSearchArgs(query="项目代号是什么"), context
    )

    assert result.status == "success"
    assert result.sources
    assert result.sources[0]["kind"] == "file"
    assert result.sources[0]["file_id"] == file_id
    assert result.sources[0]["title"] == "项目说明.txt"
    assert result.data["searched_file_ids"] == [file_id]


async def test_foreign_attachment_is_skipped_by_default(client: AsyncClient) -> None:
    conversation = await create_conversation(client)
    other_id = await create_other_users_attachment()
    context = await make_context(
        conversation_id=conversation["id"], attachment_ids=(other_id,)
    )

    result = await DocumentSearchTool(get_settings()).run(
        DocumentSearchArgs(query="内容"), context
    )

    assert result.status == "empty"
    assert result.data["skipped_file_ids"] == [other_id]


async def test_foreign_attachment_is_denied_when_explicit(client: AsyncClient) -> None:
    conversation = await create_conversation(client)
    other_id = await create_other_users_attachment()
    context = await make_context(
        conversation_id=conversation["id"], attachment_ids=(other_id,)
    )

    with pytest.raises(ToolError) as excinfo:
        await DocumentSearchTool(get_settings()).run(
            DocumentSearchArgs(query="内容", file_ids=[other_id]), context
        )

    assert excinfo.value.code == "file_scope_denied"


async def test_cross_conversation_attachment_is_skipped(client: AsyncClient) -> None:
    first = await create_conversation(client, "会话一")
    second = await create_conversation(client, "会话二")
    file_id = await upload_document(client, first["id"])
    context = await make_context(
        conversation_id=second["id"], attachment_ids=(file_id,)
    )

    result = await DocumentSearchTool(get_settings()).run(
        DocumentSearchArgs(query="项目代号"), context
    )

    assert result.status == "empty"
    assert result.data["skipped_file_ids"] == [file_id]


async def test_unready_attachment_is_unavailable(client: AsyncClient) -> None:
    user_id = await current_user_id()
    async with SessionLocal() as session:
        attachment = Attachment(
            user_id=user_id,
            conversation_id=None,
            original_name="待解析.txt",
            storage_key=f"{user_id}/pending.txt",
            extension=".txt",
            size_bytes=1,
            parse_status="pending",
        )
        session.add(attachment)
        await session.commit()
        file_id = attachment.id

    context = await make_context(conversation_id=None, attachment_ids=(file_id,))
    with pytest.raises(ToolUnavailable):
        await DocumentSearchTool(get_settings()).run(
            DocumentSearchArgs(query="内容"), context
        )


async def test_no_attachments_returns_empty(client: AsyncClient) -> None:
    conversation = await create_conversation(client)
    context = await make_context(conversation_id=conversation["id"], attachment_ids=())

    result = await DocumentSearchTool(get_settings()).run(
        DocumentSearchArgs(query="内容"), context
    )

    assert result.status == "empty"


async def test_top_k_limits_sources(client: AsyncClient) -> None:
    conversation = await create_conversation(client)
    file_id = await upload_document(client, conversation["id"])
    context = await make_context(
        conversation_id=conversation["id"], attachment_ids=(file_id,)
    )

    result = await DocumentSearchTool(get_settings()).run(
        DocumentSearchArgs(query="项目 部署 回滚", top_k=1), context
    )

    assert result.status == "success"
    assert len(result.sources) == 1
