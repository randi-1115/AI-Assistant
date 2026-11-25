"""系统能力查询。

前端用它决定哪些入口该置灰。原则是**诚实**：没实现的功能就返回 false，
不能报告一个「看起来能用」的能力。P0 只实现了文本聊天与流式，所以
web_search / document_qa / memory 一律 false。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.ai.providers.base import Capability, LLMProvider
from app.ai.providers.registry import llm_provider_dep
from app.core.config import get_settings
from app.search import search_available

router = APIRouter(tags=["system"])


@router.get("/capabilities")
async def capabilities(provider: LLMProvider = Depends(llm_provider_dep)) -> dict[str, bool]:
    settings = get_settings()
    return {
        # 由当前 provider 推导：mock 和 deepseek 的支持情况会真实反映出来。
        "text_chat": provider.supports(Capability.TEXT_CHAT),
        "streaming": provider.supports(Capability.STREAMING),
        "vision": provider.supports(Capability.VISION),
        "tool_calling": provider.supports(Capability.TOOL_CALLING),
        # agent 模式需要有启用的工具；原生工具调用之外还有结构化兜底。
        "agent_mode": bool(settings.tools_enabled_names)
        and provider.supports(Capability.TEXT_CHAT),
        # 文件问答已实现；联网搜索取决于是否配置了真实 key。
        "web_search": search_available(settings),
        "document_qa": True,
        "memory": False,
    }
