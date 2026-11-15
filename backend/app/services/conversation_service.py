"""会话的增删改查编排。

薄薄一层：负责归属校验和错误转换，SQL 都交给 repository。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConversationNotFound
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.storage import get_storage
from app.storage.base import StorageBackend, StorageError

logger = logging.getLogger(__name__)

DEFAULT_MESSAGE_PAGE_SIZE = 50


class ConversationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: StorageBackend | None = None,
    ) -> None:
        self.session = session
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.attachments = AttachmentRepository(session)
        self.storage = storage or get_storage()

    async def list_conversations(
        self, user_id: int, *, keyword: str | None = None, page: int = 1, page_size: int = 20
    ) -> tuple[list[Conversation], int]:
        return await self.conversations.list_by_user(
            user_id, keyword=keyword, limit=page_size, offset=(page - 1) * page_size
        )

    async def get_conversation(self, conversation_id: int, user_id: int) -> Conversation:
        conversation = await self.conversations.get_owned(conversation_id, user_id)
        if conversation is None:
            # 别人的会话和不存在的会话返回同一个错误，不泄露资源是否存在。
            raise ConversationNotFound()
        return conversation

    async def create_conversation(self, user_id: int, title: str) -> Conversation:
        conversation = await self.conversations.create(user_id, title)
        await self.session.commit()
        return conversation

    async def rename_conversation(
        self, conversation_id: int, user_id: int, title: str
    ) -> Conversation:
        conversation = await self.conversations.rename(conversation_id, user_id, title)
        if conversation is None:
            raise ConversationNotFound()
        await self.session.commit()
        return conversation

    async def delete_conversation(self, conversation_id: int, user_id: int) -> None:
        conversation = await self.conversations.get_owned(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFound()
        # 数据库外键会级联删除附件与分块，但删不掉磁盘文件。先取出 key，
        # 提交后再做 best-effort 物理删除。
        storage_keys = await self.attachments.list_storage_keys_by_conversation(
            conversation_id, user_id
        )
        deleted = await self.conversations.delete(conversation_id, user_id)
        if not deleted:
            raise ConversationNotFound()
        await self.session.commit()

        for storage_key in storage_keys:
            try:
                await self.storage.delete(storage_key)
            except StorageError:
                logger.warning("删除会话时清理磁盘文件失败 key=%s", storage_key)

    async def list_messages(
        self,
        conversation_id: int,
        user_id: int,
        *,
        before_id: int | None = None,
        limit: int = DEFAULT_MESSAGE_PAGE_SIZE,
    ) -> list[Message]:
        # 先确认会话归属，再读消息——消息查询本身不带 user_id 条件。
        await self.get_conversation(conversation_id, user_id)
        max_id = before_id - 1 if before_id is not None else None
        return await self.messages.list_recent(conversation_id, limit=limit, max_id=max_id)
