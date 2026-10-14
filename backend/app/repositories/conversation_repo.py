"""conversations 表的读写。

**所有查询都强制带 user_id 条件**。get_owned 是归属校验的唯一入口——任何
按 id 取会话的地方都必须经过它，绝不能只凭前端传来的 id 就认为有权访问。
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import NOW_MS
from app.models.conversation import Conversation


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_by_user(
        self,
        user_id: int,
        *,
        keyword: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Conversation], int]:
        conditions = [Conversation.user_id == user_id, Conversation.status == "active"]
        if keyword:
            conditions.append(Conversation.title.contains(keyword))

        total = await self.session.scalar(
            select(func.count()).select_from(Conversation).where(*conditions)
        )

        result = await self.session.execute(
            select(Conversation)
            .where(*conditions)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all()), int(total or 0)

    async def get_owned(self, conversation_id: int, user_id: int) -> Conversation | None:
        """按 id 取会话，同时校验归属。取不到或不属于该用户都返回 None。"""
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.status == "active",
            )
        )
        return result.scalar_one_or_none()

    async def create(self, user_id: int, title: str) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title)
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def rename(self, conversation_id: int, user_id: int, title: str) -> Conversation | None:
        conversation = await self.get_owned(conversation_id, user_id)
        if conversation is None:
            return None
        conversation.title = title
        await self.session.flush()
        return conversation

    async def delete(self, conversation_id: int, user_id: int) -> bool:
        conversation = await self.get_owned(conversation_id, user_id)
        if conversation is None:
            return False
        # 消息由数据库 ON DELETE CASCADE 连带删除。
        await self.session.delete(conversation)
        await self.session.flush()
        return True

    async def touch(self, conversation_id: int) -> None:
        """新消息之后刷新 updated_at，让会话列表按最近活跃排序。

        直接发 UPDATE 而不改 ORM 对象：Conversation 有 onupdate，但对象没有
        其他字段变化时 SQLAlchemy 不会生成 UPDATE 语句。
        """
        await self.session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=NOW_MS)
        )

    async def set_title_if_default(self, conversation_id: int, title: str, default: str) -> bool:
        """只在标题仍是默认值时才改——避免覆盖用户手动起的名字。

        返回是否真的更新了，便于调用方记录。
        """
        result = await self.session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.title == default)
            .values(title=title)
        )
        return bool(result.rowcount)
