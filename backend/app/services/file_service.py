"""文件上传、查询、删除的编排。

上传链路刻意做成「同步解析」：响应返回时 parse_status 已经落定为
ready / failed。解析放在线程里跑，避免阻塞事件循环；写分块和最终状态用
独立 session，长解析期间不持有请求事务。
"""

from __future__ import annotations

import functools
import logging
from typing import Any

import anyio
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import (
    ConversationNotFound,
    FileNotFound,
    FileTooLarge,
    InvalidFileContent,
    InvalidFileName,
    StorageFailure,
    UnsupportedFileType,
)
from app.db.session import SessionLocal
from app.models.attachment import (
    PARSE_FAILED,
    PARSE_PROCESSING,
    PARSE_READY,
    Attachment,
)
from app.models.document_chunk import DocumentChunk
from app.rag.chunker import Chunk, chunk_document
from app.rag.parser_base import InvalidFilenameError, ParseError, UnsupportedFormatError
from app.rag.parsers import (
    ensure_supported_extension,
    parse_document,
    sanitize_filename,
    sniff_file,
)
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.conversation_repo import ConversationRepository
from app.storage import get_storage
from app.storage.base import (
    EmptyFileError,
    FileTooLargeError,
    StorageBackend,
    StorageError,
)

logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_CHARS = 1000


def attachment_snapshot(attachment: Attachment) -> dict[str, Any]:
    """写进用户消息 metadata_json 的附件快照，用于刷新后展示与重新生成。"""
    snapshot: dict[str, Any] = {
        "id": attachment.id,
        "name": attachment.original_name,
        "parse_status": attachment.parse_status,
        "supported_for_qa": attachment.supported_for_qa,
    }
    if attachment.error_message:
        snapshot["error_message"] = attachment.error_message
    return snapshot


class FileService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
        storage: StorageBackend | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.session_factory = session_factory
        self.storage = storage or get_storage()
        self.settings = settings or get_settings()
        self.repo = AttachmentRepository(session)

    # --- 上传 ---

    async def upload(
        self,
        *,
        user_id: int,
        upload: UploadFile,
        conversation_id: int | None = None,
    ) -> Attachment:
        try:
            original_name, extension = sanitize_filename(upload.filename)
            ensure_supported_extension(extension)
        except InvalidFilenameError as exc:
            raise InvalidFileName(str(exc)) from exc
        except UnsupportedFormatError as exc:
            raise UnsupportedFileType(str(exc)) from exc

        if conversation_id is not None:
            conversation = await ConversationRepository(self.session).get_owned(
                conversation_id, user_id
            )
            if conversation is None:
                raise ConversationNotFound()

        max_bytes = self.settings.max_upload_mb * 1024 * 1024
        if upload.size is not None and upload.size > max_bytes:
            raise FileTooLarge()

        try:
            stored = await self.storage.save(
                upload.file,
                user_id=user_id,
                extension=extension,
                max_bytes=max_bytes,
            )
        except FileTooLargeError as exc:
            raise FileTooLarge(str(exc)) from exc
        except EmptyFileError as exc:
            raise InvalidFileContent(str(exc)) from exc
        except StorageError as exc:
            raise StorageFailure(str(exc)) from exc

        path = self.storage.path_for(stored.storage_key)
        try:
            mime_type = sniff_file(path, extension)
        except UnsupportedFormatError as exc:
            await self._delete_quietly(stored.storage_key)
            raise InvalidFileContent(str(exc)) from exc
        except OSError as exc:
            await self._delete_quietly(stored.storage_key)
            raise StorageFailure(f"读取上传文件失败：{exc}") from exc

        try:
            attachment = await self.repo.create(
                user_id=user_id,
                conversation_id=conversation_id,
                original_name=original_name,
                storage_key=stored.storage_key,
                extension=extension,
                size_bytes=stored.size_bytes,
                mime_type=mime_type,
                sha256=stored.sha256,
                parse_status=PARSE_PROCESSING,
            )
            await self.session.commit()
        except Exception:
            await self._delete_quietly(stored.storage_key)
            raise

        await self._parse_and_store(attachment, path, extension)
        await self.session.refresh(attachment)
        return attachment

    async def _parse_and_store(
        self, attachment: Attachment, path: Any, extension: str
    ) -> None:
        try:
            document = await anyio.to_thread.run_sync(
                functools.partial(
                    parse_document,
                    path,
                    extension=extension,
                    max_chars=self.settings.rag_max_extracted_chars,
                )
            )
            chunks = chunk_document(
                document,
                chunk_size=self.settings.rag_chunk_size_chars,
                overlap=self.settings.rag_chunk_overlap_chars,
                max_chunks=self.settings.rag_max_chunks_per_file,
            )
            if not chunks:
                raise ParseError("未提取到可用文本（切块结果为空）")
        except ParseError as exc:
            await self._mark_failed(attachment.id, str(exc))
            return
        except Exception:
            logger.exception("附件解析出现未预期异常 file_id=%s", attachment.id)
            await self._mark_failed(attachment.id, "解析过程出现内部错误")
            return

        await self._store_chunks(attachment.id, chunks)

    async def _store_chunks(self, attachment_id: int, chunks: list[Chunk]) -> None:
        try:
            async with self.session_factory() as session:
                repo = AttachmentRepository(session)
                await repo.bulk_create_chunks(
                    [
                        DocumentChunk(
                            attachment_id=attachment_id,
                            chunk_index=chunk.index,
                            section_title=chunk.section_title,
                            content=chunk.content,
                            char_count=chunk.char_count,
                            metadata_json=chunk.metadata or None,
                        )
                        for chunk in chunks
                    ]
                )
                await repo.set_status(attachment_id, PARSE_READY, None)
                await session.commit()
        except Exception:
            logger.exception("写入文档分块失败 file_id=%s", attachment_id)
            await self._mark_failed(attachment_id, "写入文档分块失败")

    async def _mark_failed(self, attachment_id: int, message: str) -> None:
        try:
            async with self.session_factory() as session:
                await AttachmentRepository(session).set_status(
                    attachment_id,
                    PARSE_FAILED,
                    message[:MAX_ERROR_MESSAGE_CHARS],
                )
                await session.commit()
        except Exception:
            # 启动清理会兜底把残留的 processing 标成 failed。
            logger.exception("更新附件失败状态失败 file_id=%s", attachment_id)

    # --- 查询 / 删除 ---

    async def get(self, file_id: int, user_id: int) -> Attachment:
        attachment = await self.repo.get_owned(file_id, user_id)
        if attachment is None:
            raise FileNotFound()
        return attachment

    async def delete(self, file_id: int, user_id: int) -> None:
        attachment = await self.repo.get_owned(file_id, user_id)
        if attachment is None:
            raise FileNotFound()
        storage_key = attachment.storage_key
        deleted = await self.repo.delete_owned(file_id, user_id)
        if not deleted:
            raise FileNotFound()
        await self.session.commit()
        # 数据库删除成功后再删磁盘；磁盘删除失败只记日志，避免出现
        # 「元数据还在但文件已经没了」的情况。
        await self._delete_quietly(storage_key)

    async def _delete_quietly(self, storage_key: str) -> None:
        try:
            await self.storage.delete(storage_key)
        except StorageError:
            logger.warning("删除磁盘文件失败 key=%s", storage_key)
