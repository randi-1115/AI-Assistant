"""users 表：预留的用户主体。

第一阶段不做登录认证，只 seed 一条固定的开发用户；但所有业务查询都已经
按 user_id 过滤，后续补认证时不需要改数据模型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BIGINT_UNSIGNED, Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BIGINT_UNSIGNED, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    # 首版不用来登录。补齐认证之前，这个字段为空绝不代表账号是安全的。
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<User id={self.id} email={self.email!r}>"
