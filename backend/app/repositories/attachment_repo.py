"""attachments / document_chunks 的读写。

**所有查询都强制带 user_id 条件**。get_owned、delete_owned、list_candidates
是归属校验的入口；调用方不能只凭前端传来的 file_id 就认为有权访问。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attachment import (
    PARSE_FAILED,
    PARSE_PENDING,
    PARSE_PROCESSING,
    PARSE_READY,
    Attachment,
)
from app.models.document_chunk import DocumentChunk


class AttachmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        conversation_id: int | None,
        original_name: str,
        storage_key: str,
        extension: str,
        size_bytes: int,
        mime_type: str | None = None,
        sha256: str | None = None,
        parse_status: str = PARSE_PENDING,
        error_message: str | None = None,
    ) -> Attachment:
        attachment = Attachment(
            user_id=user_id,
            conversation_id=conversation_id,
            original_name=original_name,
            storage_key=storage_key,
            mime_type=mime_type,
            extension=extension,
            size_bytes=size_bytes,
            sha256=sha256,
            parse_status=parse_status,
            error_message=error_message,
        )
        self.session.add(attachment)
        await self.session.flush()
        return attachment

    async def get_owned(self, file_id: int, user_id: int) -> Attachment | None:
        result = await self.session.execute(
            select(Attachment).where(
                Attachment.id == file_id,
                Attachment.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_owned_by_ids(self, user_id: int, file_ids: Sequence[int]) -> list[Attachment]:
        if not file_ids:
            return []
        result = await self.session.execute(
            select(Attachment).where(
                Attachment.user_id == user_id,
                Attachment.id.in_(list(file_ids)),
            )
        )
        return list(result.scalars().all())

    async def set_status(
        self,
        file_id: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        await self.session.execute(
            update(Attachment)
            .where(Attachment.id == file_id)
            .values(parse_status=status, error_message=error_message)
        )

    async def delete_owned(self, file_id: int, user_id: int) -> bool:
        result = await self.session.execute(
            delete(Attachment).where(
                Attachment.id == file_id,
                Attachment.user_id == user_id,
            )
        )
        return bool(result.rowcount)

    async def list_storage_keys_by_conversation(
        self, conversation_id: int, user_id: int
    ) -> list[str]:
        """删除会话前先取出磁盘 key；数据库级联删不掉磁盘文件。"""
        result = await self.session.execute(
            select(Attachment.storage_key).where(
                Attachment.conversation_id == conversation_id,
                Attachment.user_id == user_id,
            )
        )
        return [row[0] for row in result.all()]

    async def claim_conversation(
        self, user_id: int, file_ids: Sequence[int], conversation_id: int
    ) -> None:
        """把还没有归属会话的附件绑定到当前会话。

        这样在「先上传、后建会话」的首轮流程里，删除会话也能级联清理这些文件。
        """
        if not file_ids:
            return
        await self.session.execute(
            update(Attachment)
            .where(
                Attachment.user_id == user_id,
                Attachment.id.in_(list(file_ids)),
                Attachment.conversation_id.is_(None),
            )
            .values(conversation_id=conversation_id)
        )

    async def bulk_create_chunks(self, chunks: Sequence[DocumentChunk]) -> None:
        if not chunks:
            return
        self.session.add_all(list(chunks))
        await self.session.flush()

    async def list_candidates(
        self,
        user_id: int,
        attachment_ids: Sequence[int],
        *,
        limit: int,
    ) -> list[tuple[DocumentChunk, Attachment]]:
        """取候选块：只取当前用户、已 ready、且指定附件范围内的块。"""
        if not attachment_ids:
            return []
        result = await self.session.execute(
            select(DocumentChunk, Attachment)
            .join(Attachment, DocumentChunk.attachment_id == Attachment.id)
            .where(
                Attachment.user_id == user_id,
                Attachment.parse_status == PARSE_READY,
                Attachment.id.in_(list(attachment_ids)),
            )
            .order_by(
                Attachment.created_at.desc(),
                Attachment.id.desc(),
                DocumentChunk.chunk_index.asc(),
            )
            .limit(limit)
        )
        return [(chunk, attachment) for chunk, attachment in result.all()]

    async def fail_incomplete(self) -> int:
        """启动时把上次异常退出遗留的 pending/processing 附件标为 failed。"""
        result = await self.session.execute(
            update(Attachment)
            .where(Attachment.parse_status.in_((PARSE_PENDING, PARSE_PROCESSING)))
            .values(
                parse_status=PARSE_FAILED,
                error_message="解析未完成（服务重启或中断），请重新上传",
            )
        )
        return int(result.rowcount or 0)

    async def update_metadata(self, file_id: int, **values: Any) -> None:
        """保留给后续阶段的小工具；当前链路未使用。"""
        await self.session.execute(
            update(Attachment).where(Attachment.id == file_id).values(**values)
        )
