"""attachments 表：上传文件元数据。

文件正文在私有存储里，这里只存元数据和内部 storage_key；storage_key 永不
返回给客户端。parse_status 的取值：
    pending      —— 已入库，等待解析（同步链路上通常一闪而过）
    processing   —— 正在解析
    ready        —— 文本已提取并切块，可参与问答
    failed       —— 解析失败（空文本、损坏文件等），error_message 说明原因
    unsupported  —— 格式合法但暂不支持文本问答
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CHAR, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_UNSIGNED, DATETIME_MS, NOW_MS, Base

PARSE_PENDING = "pending"
PARSE_PROCESSING = "processing"
PARSE_READY = "ready"
PARSE_FAILED = "failed"
PARSE_UNSUPPORTED = "unsupported"

PARSE_STATUSES = (
    PARSE_PENDING,
    PARSE_PROCESSING,
    PARSE_READY,
    PARSE_FAILED,
    PARSE_UNSUPPORTED,
)


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        # 文件列表按用户 + 创建时间倒序，这个复合索引直接覆盖。
        Index("ix_attachments_user_created", "user_id", "created_at"),
        Index("ix_attachments_conversation_id", "conversation_id"),
    )
    # 只声明 created_at，没有 updated_at；仍然需要 eager_defaults，否则
    # 异步会话里序列化 created_at 会触发同步 IO（MissingGreenlet）。
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BIGINT_UNSIGNED, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BIGINT_UNSIGNED, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 附件归属某个会话，删除会话时级联删除。上传时还没有会话则为 NULL，
    # 首次作为聊天附件使用时再归属到当前会话。
    conversation_id: Mapped[int | None] = mapped_column(
        BIGINT_UNSIGNED,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 内部相对键，不向客户端暴露，也不参与路径拼接前的信任判断。
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(150), nullable=True)
    extension: Mapped[str] = mapped_column(String(20), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BIGINT_UNSIGNED, nullable=False)
    sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    parse_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PARSE_PENDING, server_default=PARSE_PENDING
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, server_default=NOW_MS, nullable=False
    )

    @property
    def file_id(self) -> int:
        return self.id

    @property
    def supported_for_qa(self) -> bool:
        return self.parse_status == PARSE_READY

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Attachment id={self.id} name={self.original_name!r} status={self.parse_status!r}>"
