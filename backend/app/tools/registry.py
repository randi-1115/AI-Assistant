"""工具注册表：白名单、参数校验、超时与统一错误。

调用方（AgentService）只需要：
    registry.definitions(context)  -> 给模型的工具定义
    registry.execute(name, args, context) -> ToolExecution

状态映射（P2 第 4/6 项直接复用）：

    ToolExecution.status   SSE tool_status   tool_runs.status
    success                done              success
    empty                  empty             success
    unavailable            unavailable       failed
    failed                 failed            failed
    timeout                failed            timeout

`execute` 永远不把工具异常抛给调用方（`asyncio.CancelledError` 除外）：
失败被转换成带 `error_code` 的结果，方便循环把错误回填给模型。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from app.tools.base import Tool, ToolContext, ToolError, ToolResult, ToolUnavailable

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from app.core.config import Settings
    from app.search.service import SearchService

logger = logging.getLogger(__name__)

ExecutionStatus = Literal["success", "empty", "unavailable", "failed", "timeout"]
SSEStatus = Literal["done", "empty", "unavailable", "failed"]
ToolRunStatus = Literal["success", "failed", "timeout"]

TRUNCATION_MARKER = "…（输出已截断）"
MAX_ERROR_CHARS = 500


@dataclass(frozen=True, slots=True)
class ToolExecution:
    """一次工具调用的完整结果，可直接用于 SSE、日志和 tool_runs。"""

    tool: str
    status: ExecutionStatus
    content: str = ""
    message: str | None = None
    sources: tuple[dict[str, Any], ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    duration_ms: int = 0
    truncated: bool = False
    arguments: dict[str, Any] = field(default_factory=dict)

    @property
    def sse_status(self) -> SSEStatus:
        if self.status == "success":
            return "done"
        if self.status == "empty":
            return "empty"
        if self.status == "unavailable":
            return "unavailable"
        return "failed"

    @property
    def tool_run_status(self) -> ToolRunStatus:
        if self.status in ("success", "empty"):
            return "success"
        if self.status == "timeout":
            return "timeout"
        return "failed"


class ToolRegistry:
    """工具白名单 + 统一执行入口。

    allowed=None 表示注册的工具都可启用；空集合表示全部禁用。
    运行时 enable/disable 只影响当前进程，配置白名单始终是上限。
    """

    def __init__(
        self,
        *,
        allowed: Collection[str] | None = None,
        timeout_seconds: float = 15.0,
        max_output_chars: int = 8000,
    ) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        self._allowed = None if allowed is None else frozenset(allowed)
        self._disabled: set[str] = set()
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    # --- 注册与启停 ---

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def register(self, tool: Tool[Any]) -> None:
        name = getattr(tool, "name", "")
        if not name:
            raise ValueError("工具必须声明非空的 name")
        if name in self._tools:
            raise ValueError(f"工具名重复：{name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    def enable(self, name: str) -> None:
        if name not in self._tools:
            raise ValueError(f"未注册的工具：{name}")
        self._disabled.discard(name)

    def disable(self, name: str) -> None:
        if name not in self._tools:
            raise ValueError(f"未注册的工具：{name}")
        self._disabled.add(name)

    def is_enabled(self, name: str) -> bool:
        if name not in self._tools or name in self._disabled:
            return False
        return self._allowed is None or name in self._allowed

    def enabled(self, context: ToolContext) -> list[Tool[Any]]:
        """白名单内的工具（不检查运行时可用性）。"""
        return [tool for name, tool in self._tools.items() if self.is_enabled(name)]

    def definitions(self, context: ToolContext) -> list[dict[str, Any]]:
        """暴露给模型的工具定义；不可用或检查出错的工具会被隐藏。"""
        definitions: list[dict[str, Any]] = []
        for tool in self.enabled(context):
            if self._check_available(tool, context):
                definitions.append(tool.definition())
        return definitions

    def availability(self, name: str, context: ToolContext) -> tuple[bool, str | None]:
        """(是否可用, 不可用原因)，用于只读工具列表与调试。"""
        tool = self._tools.get(name)
        if tool is None:
            return False, "工具未注册"
        if self._check_available(tool, context):
            return True, None
        return False, self._unavailable_reason(tool, context)

    def effective_timeout(self, tool: Tool[Any]) -> float:
        """单工具实际生效的超时：工具值与全局上限取较小者。"""
        candidate = self.timeout_seconds
        if tool.timeout_seconds is not None:
            candidate = min(tool.timeout_seconds, self.timeout_seconds)
        return max(0.1, candidate)

    # --- 执行 ---

    async def execute(
        self, name: str, raw_args: object, context: ToolContext
    ) -> ToolExecution:
        started = time.perf_counter()
        execution = await self._execute(name, raw_args, context)
        execution = replace(
            execution, duration_ms=int((time.perf_counter() - started) * 1000)
        )
        logger.info(
            "tool_run tool=%s status=%s error=%s duration_ms=%s request_id=%s",
            execution.tool,
            execution.status,
            execution.error_code,
            execution.duration_ms,
            context.request_id,
        )
        return execution

    async def _execute(
        self, name: str, raw_args: object, context: ToolContext
    ) -> ToolExecution:
        tool = self._tools.get(name)
        if tool is None:
            return self._failure(
                name, "unknown_tool", f"未知工具：{name}。可用工具：{self._enabled_names()}"
            )
        if not self.is_enabled(name):
            return self._failure(name, "tool_disabled", f"工具已禁用：{name}")
        if not self._check_available(tool, context):
            reason = self._unavailable_reason(tool, context)
            message = reason or f"工具当前不可用：{name}"
            return ToolExecution(
                tool=name,
                status="unavailable",
                content=message,
                message=message,
                error_code="tool_unavailable",
                arguments={} if not isinstance(raw_args, dict) else dict(raw_args),
            )
        if not isinstance(raw_args, dict):
            return self._failure(name, "invalid_arguments", "工具参数必须是 JSON 对象")

        try:
            args = tool.args_model.model_validate(raw_args)
        except ValidationError as exc:
            return self._failure(
                name,
                "invalid_arguments",
                f"工具参数不合法：{self._format_validation_error(exc)}",
                arguments=raw_args,
            )

        timeout = self.effective_timeout(tool)
        try:
            result = await asyncio.wait_for(tool.run(args, context), timeout=timeout)
        except asyncio.CancelledError:
            # 取消是控制信号，不能伪装成工具失败，否则会破坏 ASGI 的取消语义。
            raise
        except TimeoutError:
            message = f"工具执行超时（{timeout:g}s）"
            return ToolExecution(
                tool=name,
                status="timeout",
                content=message,
                message=message,
                error_code="timeout",
                arguments=raw_args,
            )
        except ToolUnavailable as exc:
            return ToolExecution(
                tool=name,
                status="unavailable",
                content=f"工具不可用：{exc.message}",
                message=exc.message,
                error_code=exc.code,
                arguments=raw_args,
            )
        except ToolError as exc:
            return self._failure(
                name, exc.code, f"工具执行失败：{exc.message}", arguments=raw_args
            )
        except Exception:  # noqa: BLE001 - 工具异常不能穿透到聊天流
            logger.exception("工具执行出现未预期异常 tool=%s", name)
            return self._failure(
                name, "internal_error", "工具执行失败：内部错误", arguments=raw_args
            )

        if not isinstance(result, ToolResult):
            logger.error(
                "工具返回类型不合法 tool=%s type=%s", name, type(result).__name__
            )
            return self._failure(
                name, "internal_error", "工具执行失败：返回类型不合法", arguments=raw_args
            )

        content, truncated = self._truncate(result.content, self._effective_cap(tool))
        if not content and result.status != "success":
            content = result.message or (
                "工具没有返回结果" if result.status == "empty" else "工具当前不可用"
            )
        return ToolExecution(
            tool=name,
            status=result.status,
            content=content,
            message=result.message,
            sources=result.sources,
            data=dict(result.data),
            # 工具自己判断“当前不可用”时也要留下机器码，方便 tool_runs 追溯。
            error_code="tool_unavailable" if result.status == "unavailable" else None,
            duration_ms=0,
            truncated=truncated,
            arguments=raw_args,
        )

    # --- 内部工具方法 ---

    def _failure(
        self,
        name: str,
        code: str,
        message: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> ToolExecution:
        return ToolExecution(
            tool=name,
            status="failed",
            content=message,
            message=message,
            error_code=code,
            arguments=dict(arguments or {}),
        )

    def _enabled_names(self) -> str:
        names = [name for name in self._tools if self.is_enabled(name)]
        return "、".join(names) if names else "无"

    def _check_available(self, tool: Tool[Any], context: ToolContext) -> bool:
        try:
            return tool.is_available(context)
        except Exception:  # noqa: BLE001 - 可用性检查失败按不可用处理
            logger.exception("工具可用性检查失败 tool=%s", tool.name)
            return False

    def _unavailable_reason(self, tool: Tool[Any], context: ToolContext) -> str | None:
        try:
            return tool.unavailable_reason(context)
        except Exception:  # noqa: BLE001
            logger.exception("工具不可用原因获取失败 tool=%s", tool.name)
            return None

    def _effective_cap(self, tool: Tool[Any]) -> int:
        if tool.max_output_chars is None:
            return self.max_output_chars
        return max(0, min(tool.max_output_chars, self.max_output_chars))

    @staticmethod
    def _truncate(content: str, cap: int) -> tuple[str, bool]:
        if len(content) <= cap:
            return content, False
        if cap <= len(TRUNCATION_MARKER):
            return content[:cap], True
        return content[: cap - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER, True

    @staticmethod
    def _format_validation_error(exc: ValidationError) -> str:
        parts: list[str] = []
        for error in exc.errors():
            location = ".".join(str(item) for item in error.get("loc", ())) or "arguments"
            parts.append(f"{location}: {error.get('msg', '参数不合法')}")
        return "; ".join(parts)[:MAX_ERROR_CHARS]


def build_default_registry(
    settings: "Settings",
    *,
    search_service: "SearchService | None" = None,
) -> ToolRegistry:
    """按配置装配默认工具集。

    白名单里出现未注册的名称只告警并忽略：配置是部署问题，不该让服务起不来。
    """
    # 延迟导入，避免 tools 包在 import 时反向依赖具体实现/搜索服务。
    from app.tools.calculator import CalculatorTool
    from app.tools.document_search import DocumentSearchTool
    from app.tools.web_search import WebSearchTool

    registry = ToolRegistry(
        allowed=settings.tools_enabled_names,
        timeout_seconds=settings.tool_timeout_seconds,
        max_output_chars=settings.tool_max_output_chars,
    )
    registry.register(CalculatorTool())
    registry.register(DocumentSearchTool(settings))
    registry.register(WebSearchTool(settings, search_service))

    unknown = settings.tools_enabled_names - set(registry.names)
    if unknown:
        logger.warning(
            "TOOLS_ENABLED 中未注册的工具已忽略: %s", ", ".join(sorted(unknown))
        )
    return registry
