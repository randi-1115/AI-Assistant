"""供应商中立的 LLM 契约。

业务层（services/）只依赖本模块，永不 import 任何供应商 SDK。将来换供应商
只需要新增一个 adapter 并注册，服务和 endpoint 一行都不用改。

异常刻意复用 app.core.errors 里的 LLM* 家族，provider 的职责是把上游各式
各样的错误**映射**成它们。上游响应的原始正文只进日志，绝不进响应体——
那里面可能有账号信息，也可能回显请求头里的 API Key。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


class Capability(StrEnum):
    TEXT_CHAT = "text_chat"
    STREAMING = "streaming"
    VISION = "vision"
    TOOL_CALLING = "tool_calling"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatOptions:
    temperature: float | None = None
    max_output_tokens: int | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ChatUsage:
    """token 用量。供应商没返回时两个字段都是 None——不要用 0 冒充。"""

    input_tokens: int | None = None
    output_tokens: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    model_name: str | None = None
    finish_reason: str | None = None
    usage: ChatUsage | None = None


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """流式事件。type 只有三种：

    token —— 一段新文本
    usage —— 用量信息（可能不出现，取决于供应商）
    done  —— 流结束，带 finish_reason
    """

    type: Literal["token", "usage", "done"]
    text: str | None = None
    usage: ChatUsage | None = None
    finish_reason: str | None = None

    @classmethod
    def token(cls, text: str) -> "StreamEvent":
        return cls(type="token", text=text)

    @classmethod
    def usage_event(cls, usage: ChatUsage) -> "StreamEvent":
        return cls(type="usage", usage=usage)

    @classmethod
    def done(cls, finish_reason: str = "stop") -> "StreamEvent":
        return cls(type="done", finish_reason=finish_reason)


class LLMProvider(ABC):
    """统一模型接口。

    stream_chat 刻意声明为普通 def（不是 async def）：实现方用 async generator，
    调用方直接 `async for`，不要 await。
    """

    name: str = "unknown"
    _capabilities: frozenset[Capability] = frozenset()

    @property
    def capabilities(self) -> frozenset[Capability]:
        return self._capabilities

    def supports(self, capability: Capability) -> bool:
        return capability in self._capabilities

    @abstractmethod
    async def chat(
        self, messages: Sequence[ChatMessage], options: ChatOptions | None = None
    ) -> ChatResult:
        """一次性（非流式）对话。"""

    @abstractmethod
    def stream_chat(
        self, messages: Sequence[ChatMessage], options: ChatOptions | None = None
    ) -> AsyncIterator[StreamEvent]:
        """流式对话。实现必须是 async generator。"""
