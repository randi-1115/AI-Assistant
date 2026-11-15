"""document_chunks 表：文档切块。

首版检索是关键词重合度评分，不接 embedding，因此没有向量列。
内容用 MEDIUMTEXT（最大 16MB）；普通 TEXT 只有 64KB，长章节会被截断。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    BIGINT_UNSIGNED,
    DATETIME_MS,
    INT_UNSIGNED,
    MEDIUMTEXT,
    NOW_MS,
    Base,
)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "attachment_id",
            "chunk_index",
            name="uq_document_chunks_attachment_chunk_index",
        ),
    )
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BIGINT_UNSIGNED, primary_key=True, autoincrement=True)
    attachment_id: Mapped[int] = mapped_column(
        BIGINT_UNSIGNED,
        ForeignKey("attachments.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(INT_UNSIGNED, nullable=False)
    section_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    char_count: Mapped[int] = mapped_column(INT_UNSIGNED, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, server_default=NOW_MS, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<DocumentChunk attachment_id={self.attachment_id} index={self.chunk_index}>"
