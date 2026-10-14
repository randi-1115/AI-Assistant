"""会话与历史消息接口。契约见 docs/API-SPEC.md。

endpoint 只做参数校验、调用 service、返回响应；归属校验和错误转换在 service 层。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.db.session import get_db
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from app.schemas.conversation import (
    ConversationCreate,
    ConversationRead,
    ConversationRename,
)
from app.schemas.message import MessageRead
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ConversationRead:
    service = ConversationService(db)
    conversation = await service.create_conversation(user_id, payload.resolved_title())
    return ConversationRead.model_validate(conversation)


@router.get("", response_model=Page[ConversationRead])
async def list_conversations(
    keyword: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> Page[ConversationRead]:
    service = ConversationService(db)
    conversations, total = await service.list_conversations(
        user_id, keyword=keyword, page=page, page_size=page_size
    )
    return Page[ConversationRead](
        items=[ConversationRead.model_validate(item) for item in conversations],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ConversationRead:
    service = ConversationService(db)
    conversation = await service.get_conversation(conversation_id, user_id)
    return ConversationRead.model_validate(conversation)


@router.patch("/{conversation_id}", response_model=ConversationRead)
async def rename_conversation(
    conversation_id: int,
    payload: ConversationRename,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ConversationRead:
    service = ConversationService(db)
    conversation = await service.rename_conversation(
        conversation_id, user_id, payload.title.strip()
    )
    return ConversationRead.model_validate(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> Response:
    service = ConversationService(db)
    await service.delete_conversation(conversation_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: int,
    before_id: int | None = Query(default=None, ge=1, description="游标：只返回 id 小于它的消息"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> list[MessageRead]:
    service = ConversationService(db)
    messages = await service.list_messages(
        conversation_id, user_id, before_id=before_id, limit=limit
    )
    return [MessageRead.model_validate(message) for message in messages]
