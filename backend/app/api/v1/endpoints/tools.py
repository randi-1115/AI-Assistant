"""只读工具清单。

用途是让调用方/前端看到服务端注册了哪些工具、参数 schema 是什么、当前是否
可用。**不执行工具**：真正的调用由 agent 循环在服务端完成，避免把任意工具
执行入口暴露成 HTTP API。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user_id
from app.core.config import Settings, get_settings
from app.core.logging import get_request_id
from app.schemas.tool import ToolDefinitionOut, ToolListOut
from app.tools.base import ToolContext
from app.tools.registry import build_default_registry

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=ToolListOut)
async def list_tools(
    user_id: int = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
) -> ToolListOut:
    registry = build_default_registry(settings)
    context = ToolContext(user_id=user_id, request_id=get_request_id())

    tools: list[ToolDefinitionOut] = []
    for tool in registry.enabled(context):
        available, reason = registry.availability(tool.name, context)
        tools.append(
            ToolDefinitionOut(
                name=tool.name,
                description=tool.description,
                parameters=tool.args_model.model_json_schema(),
                timeout_seconds=registry.effective_timeout(tool),
                available=available,
                unavailable_reason=reason,
            )
        )
    return ToolListOut(tools=tools)
