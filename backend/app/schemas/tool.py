"""工具列表的只读响应结构。

这里**不包含**任何执行入口：工具调用由 AgentService 在服务端决定，
不通过 HTTP 暴露给客户端。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolDefinitionOut(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(description="Pydantic 生成的 JSON Schema")
    timeout_seconds: float = Field(gt=0)
    available: bool
    unavailable_reason: str | None = None


class ToolListOut(BaseModel):
    tools: list[ToolDefinitionOut]
