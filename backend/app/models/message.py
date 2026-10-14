"""messages 表：用户消息与助手回复。

关于 status：流式回答是「先插空行、回答完再回填」的，所以存在中间态。
    streaming   —— 助手占位消息已插入，正在接收 token
    complete    —— 正常结束
    interrupted —— 用户点了停止，或客户端断开
    failed      —— 模型调用出错（此时 content 保留已产出的部分内容）

这里刻意不加 CheckConstraint：P1 阶段很可能新增状态取值（例如软删标记），
加了约束每次都要跟着写迁移。合法取值由应用层和本文档约束。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BIGINT_UNSIGNED, DATETIME_MS, LONGTEXT, NOW_MS, Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # 按会话读取消息、以及取最近 N 条构造上下文，都走这个索引。
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_UNSIGNED, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BIGINT_UNSIGNED, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="complete", server_default="complete"
    )
    model_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    # 列名保持 metadata_json：不能叫 metadata，那会撞 SQLAlchemy Declarative 的保留属性。
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # DB-SCHEMA 里 messages 只有 created_at，没有 updated_at。
    created_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, server_default=NOW_MS, nullable=False
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Message id={self.id} role={self.role!r} status={self.status!r}>"
