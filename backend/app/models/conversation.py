"""conversations 表：会话。

(title) 默认「新对话」，首轮对话结束后由 ChatService 自动生成更合适的标题，
用户也可以随时手动重命名。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BIGINT_UNSIGNED, Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.message import Message
    from app.models.user import User

DEFAULT_CONVERSATION_TITLE = "新对话"


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (
        # 会话列表按 updated_at 倒序分页，这个复合索引直接覆盖该查询。
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_UNSIGNED, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BIGINT_UNSIGNED, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(
        String(200), nullable=False, default=DEFAULT_CONVERSATION_TITLE,
        server_default=DEFAULT_CONVERSATION_TITLE,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    # 删除会话时由数据库 ON DELETE CASCADE 连带删除消息；passive_deletes 让
    # SQLAlchemy 不要先 SELECT 出所有消息再逐条删。
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Conversation id={self.id} title={self.title!r}>"
