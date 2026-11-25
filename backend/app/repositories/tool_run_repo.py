"""tool_runs 表的写入。

调用方只写终态；错误记录失败不能影响聊天，由 AgentService 负责兜底。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool_run import ToolRun


class ToolRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        message_id: int,
        tool_name: str,
        input_json: dict[str, Any] | None,
        output_summary: str | None,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
    ) -> ToolRun:
        run = ToolRun(
            message_id=message_id,
            tool_name=tool_name,
            input_json=input_json,
            output_summary=output_summary,
            status=status,
            error_code=error_code,
            error_message=error_message,
            duration_ms=duration_ms,
        )
        self.session.add(run)
        await self.session.flush()
        return run
