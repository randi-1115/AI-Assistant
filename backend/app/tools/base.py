"""Agent 工具协议。

工具是**白名单内的本地能力**，不是任意代码执行入口：每个工具显式声明名称、
描述、Pydantic 参数模型和超时/输出上限，由 `ToolRegistry` 统一校验与执行。

设计约束：
- 工具只接收结构化参数，最终错误统一由注册表转换，不把异常抛进聊天流。
- 工具输出是**不可信数据**（网页/文件内容），调用方回填模型时必须放在
  tool 角色里，不能拼进 system 提示词。
- `args_model` 用 Pydantic 定义入参，JSON Schema 由同一个模型生成，
  保证"模型看到的 schema"和"实际校验的规则"不会漂移。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型检查，运行时不导入数据库依赖
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# 工具自身能给出的结果状态；执行失败/超时由注册表补充。
ToolResultStatus = Literal["success", "empty", "unavailable"]


class ToolError(Exception):
    """工具可预期的失败。

    `code` 是稳定的机器码（写进日志/tool_runs），`str(exc)` 是
    可以安全回填给模型的简短说明，不能包含堆栈或上游原始响应。
    """

    code: str = "tool_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.message = message
        if code is not None:
            self.code = code
        super().__init__(message)


class ToolUnavailable(ToolError):
    """依赖未配置或当前上下文下不可用（例如没配搜索 key、没有附件）。"""

    code = "tool_unavailable"


class ToolArgs(BaseModel):
    """工具参数基类：默认拒绝多余字段，避免模型悄悄传入无效参数。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass(frozen=True, slots=True)
class ToolContext:
    """单轮对话里的执行上下文，只放调用方明确授权的数据。

    `attachment_ids` 是**本轮消息**附带的文件；工具不得自行扩大这个范围。
    `session_factory` 由 AgentService 传入，工具按需自己开短会话。
    """

    user_id: int
    conversation_id: int | None = None
    attachment_ids: tuple[int, ...] = ()
    request_id: str | None = None
    session_factory: "async_sessionmaker[AsyncSession] | None" = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """工具执行结果。

    content  —— 回填给模型的文本，注册表会按上限截断。
    message  —— 给人看的简短说明（可用于 SSE tool_status）。
    sources  —— 可直接并入助手消息 metadata_json.sources 的来源载荷。
    data     —— 结构化摘要，写日志/tool_runs，不进模型上下文。
    """

    status: ToolResultStatus = "success"
    content: str = ""
    message: str | None = None
    sources: tuple[dict[str, Any], ...] = ()
    data: dict[str, Any] = field(default_factory=dict)


ArgsT = TypeVar("ArgsT", bound=BaseModel)


class Tool(ABC, Generic[ArgsT]):
    """工具基类。子类必须声明 name、description、args_model。"""

    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[ArgsT]]
    # None 表示使用全局配置（TOOL_TIMEOUT_SECONDS / TOOL_MAX_OUTPUT_CHARS）。
    timeout_seconds: float | None = None
    max_output_chars: int | None = None

    def is_available(self, context: ToolContext) -> bool:
        """当前上下文中是否可用。注册表据此决定是否暴露给模型。"""
        return True

    def unavailable_reason(self, context: ToolContext) -> str | None:
        """不可用原因，用于日志与只读工具列表。"""
        return None

    def definition(self) -> dict[str, Any]:
        """OpenAI 兼容的 function calling 定义。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }

    @abstractmethod
    async def run(self, args: ArgsT, context: ToolContext) -> ToolResult:
        """执行工具。可预期失败请抛 ToolError，不要吞掉取消信号。"""
