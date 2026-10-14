"""messages 表的读写。

调用方必须先通过 ConversationRepository.get_owned 校验会话归属——消息本身
不带 user_id，越权访问会从会话那一层被挡住。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message

STATUS_STREAMING = "streaming"
STATUS_COMPLETE = "complete"
STATUS_INTERRUPTED = "interrupted"
STATUS_FAILED = "failed"

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_recent(
        self,
        conversation_id: int,
        *,
        limit: int = 50,
        max_id: int | None = None,
    ) -> list[Message]:
        """取最近 limit 条消息，返回**升序**（便于直接渲染和组装上下文）。

        max_id 用于游标分页和重新生成：只取 id <= max_id 的消息。
        """
        statement = select(Message).where(Message.conversation_id == conversation_id)
        if max_id is not None:
            statement = statement.where(Message.id <= max_id)
        # 先按 id 倒序取「最近的」，再翻转成升序。
        statement = statement.order_by(Message.id.desc()).limit(limit)

        result = await self.session.execute(statement)
        return list(reversed(result.scalars().all()))

    async def create(
        self,
        conversation_id: int,
        *,
        role: str,
        content: str,
        status: str = STATUS_COMPLETE,
        model_provider: str | None = None,
        model_name: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            status=status,
            model_provider=model_provider,
            model_name=model_name,
            metadata_json=metadata_json,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def finalize(
        self,
        message_id: int,
        *,
        content: str,
        status: str,
        model_provider: str | None = None,
        model_name: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        """回填流式助手消息的最终内容与状态。"""
        await self.session.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(
                content=content,
                status=status,
                model_provider=model_provider,
                model_name=model_name,
                metadata_json=metadata_json,
            )
        )

    async def last_assistant(self, conversation_id: int) -> Message | None:
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.role == ROLE_ASSISTANT)
            .order_by(Message.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def last_user(self, conversation_id: int) -> Message | None:
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.role == ROLE_USER)
            .order_by(Message.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def delete_by_id(self, message_id: int) -> None:
        await self.session.execute(delete(Message).where(Message.id == message_id))

    async def fail_all_streaming(self) -> int:
        """把所有仍是 streaming 的消息标成 failed。

        应用启动时调用：进程刚起来时不可能有正在进行的流，所以这些一定是上次
        异常退出（或取消时 finalize 没写完）留下的孤儿消息。不清理的话，前端
        会永远显示一个转圈的气泡。
        """
        result = await self.session.execute(
            update(Message)
            .where(Message.status == STATUS_STREAMING)
            .values(status=STATUS_FAILED)
        )
        return int(result.rowcount or 0)
