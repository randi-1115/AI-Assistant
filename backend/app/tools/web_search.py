"""联网搜索工具：复用 P1 的 SearchService（查询规划、排序、正文抽取、缓存）。

搜索结果属于第三方不可信内容。`build_web_block` 已经把它们放进
`<web_results>` 边界并要求模型忽略其中任何指令，这里不另起格式。
"""

from __future__ import annotations

from pydantic import Field

from app.core.config import Settings
from app.search.pipeline import (
    build_web_block,
    build_web_empty_block,
    build_web_source_payloads,
    build_web_unavailable_block,
)
from app.search.service import SearchService
from app.tools.base import Tool, ToolArgs, ToolContext, ToolResult

UNAVAILABLE_REASON = (
    "联网搜索未配置，请在 backend/.env 填写 TAVILY_API_KEY 或 SERPAPI_API_KEY"
)


class WebSearchArgs(ToolArgs):
    query: str = Field(
        min_length=1,
        max_length=400,
        description="要联网搜索的问题或关键词。",
    )


class WebSearchTool(Tool[WebSearchArgs]):
    name = "web_search"
    description = (
        "联网搜索最新的公开信息，返回带标题和 URL 的来源。"
        "适合时效性、事实性、需要外部资料的问题；不要编造搜索结果。"
    )
    args_model = WebSearchArgs

    def __init__(self, settings: Settings, service: SearchService | None = None) -> None:
        self.settings = settings
        self.search_service = service or SearchService(settings)
        # 搜索结果本身就是按 SEARCH_CONTEXT_MAX_CHARS 预算拼的，这里保持一致。
        self.max_output_chars = settings.search_context_max_chars

    def is_available(self, context: ToolContext) -> bool:
        try:
            provider = self.search_service.provider
            return provider is not None and provider.configured
        except Exception:  # noqa: BLE001 - 配置探测失败按不可用处理
            return False

    def unavailable_reason(self, context: ToolContext) -> str | None:
        if self.is_available(context):
            return None
        return UNAVAILABLE_REASON

    async def run(self, args: WebSearchArgs, context: ToolContext) -> ToolResult:
        outcome = await self.search_service.search(args.query, mode="auto")
        data = {
            "provider": outcome.provider,
            "intent": outcome.intent,
            "latency_ms": outcome.latency_ms,
            "calls": outcome.calls,
            "cached": outcome.cached,
            "result_count": len(outcome.results),
        }

        if outcome.status == "ok":
            content = build_web_block(
                outcome.results,
                max_chars=self.settings.search_context_max_chars,
                intent=outcome.intent,
            )
            if not content:
                return ToolResult(
                    status="empty",
                    content=build_web_empty_block(outcome.message),
                    message=outcome.message,
                    data=data,
                )
            return ToolResult(
                content=content,
                message=outcome.message,
                sources=tuple(
                    build_web_source_payloads(
                        outcome.results,
                        snippet_chars=self.settings.search_snippet_chars,
                    )
                ),
                data=data,
            )

        if outcome.status == "empty":
            return ToolResult(
                status="empty",
                content=build_web_empty_block(outcome.message),
                message=outcome.message,
                data=data,
            )

        # disabled / unavailable：告知模型无法联网，并明确不要编造来源。
        return ToolResult(
            status="unavailable",
            content=build_web_unavailable_block(outcome.message),
            message=outcome.message,
            data=data,
        )
