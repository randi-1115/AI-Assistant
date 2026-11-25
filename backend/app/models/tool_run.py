"""tool_runs 表：每次工具执行的终态记录。

只写终态（success / failed / timeout），不写 running：running 是流式瞬态，
没有独立行可更新，写进去只会留下永远不结束的记录。
`input_json` / `output_summary` 都是**摘要**，不保存完整工具输出。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_UNSIGNED, DATETIME_MS, INT_UNSIGNED, NOW_MS, Base


class ToolRun(Base):
    __tablename__ = "tool_runs"
    __table_args__ = (
        Index("ix_tool_runs_message_created", "message_id", "created_at"),
    )
    # created_at 是数据库默认值，异步会话里要 eager_defaults 才不会触发同步 IO。
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(
        BIGINT_UNSIGNED, primary_key=True, autoincrement=True
    )
    message_id: Mapped[int] = mapped_column(
        BIGINT_UNSIGNED,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # success / failed / timeout
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(INT_UNSIGNED, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, server_default=NOW_MS, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<ToolRun id={self.id} tool={self.tool_name!r} "
            f"status={self.status!r}>"
        )
