"""Mock LLM provider：零网络、零成本，用于开发和单元测试。

只提供三件事：
  1. 固定回答   —— 返回配置好的文本，不调用任何模型
  2. 流式模拟   —— 把回答切片并加延时，模拟逐 token 到达
  3. 可控错误   —— 通过用户消息里的触发标记，或全局 MOCK_FORCE_ERROR，
                    抛出与真实供应商一一对应的异常，用来测错误分支

这个文件刻意不 import httpx，保证跑测试时不可能联网。

触发标记（写在用户消息文本里即可）：
  __mock_error__        立即抛上游错误（流还没开始吐字）
  __mock_error_after__  先吐 3 个 token 再抛，用于测「流式中途出错」
  __mock_timeout__      超时
  __mock_rate_limit__   限流
  __mock_auth__         鉴权失败
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Sequence

from app.ai.providers.base import (
    Capability,
    ChatMessage,
    ChatOptions,
    ChatResult,
    LLMProvider,
    ROLE_TOOL,
    ROLE_USER,
    StreamEvent,
    ToolCall,
)
from app.core.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)

TRIGGER_UPSTREAM = "__mock_error__"
TRIGGER_MID_STREAM = "__mock_error_after__"
TRIGGER_TIMEOUT = "__mock_timeout__"
TRIGGER_RATE_LIMIT = "__mock_rate_limit__"
TRIGGER_AUTH = "__mock_auth__"

# 工具调用标记：__mock_tool__<name> <json>，每行一个（允许出现在行内）。
# 只在本次请求真的暴露了 tools 时才生效，避免标题生成等调用被误触发。
_TOOL_MARKER = re.compile(r"__mock_tool__([a-z][a-z0-9_]*)(?:\s+(.*))?$")

# 全局强制错误：MOCK_FORCE_ERROR=upstream|timeout|rate_limit|auth
FORCE_ERROR_MAP = {
    "upstream": LLMUpstreamError,
    "timeout": LLMTimeoutError,
    "rate_limit": LLMRateLimitError,
    "auth": LLMAuthError,
}

# 中途出错前先吐几个 token，让前端能看到「已经出了一部分内容然后失败」。
MID_STREAM_TOKENS_BEFORE_ERROR = 3

DEFAULT_REPLY_TEMPLATE = (
    "这是 mock provider 的固定回答。当前 LLM_PROVIDER=mock，没有调用任何真实模型。"
    "本次请求实际带上了 {message_count} 条历史消息，所以如果这个数字随着多轮对话变大，"
    "说明上下文组装是通的。把 .env 里的 LLM_PROVIDER 改成 deepseek 并填好 "
    "LLM_API_KEY / LLM_MODEL 之后，才会走真实模型。"
)


def _last_user_content(messages: Sequence[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == ROLE_USER:
            return message.content
    return ""


class MockProvider(LLMProvider):
    name = "mock"
    _capabilities = frozenset(
        {Capability.TEXT_CHAT, Capability.STREAMING, Capability.TOOL_CALLING}
    )

    def __init__(
        self,
        *,
        chunk_size: int = 4,
        stream_delay_ms: int = 60,
        reply_template: str = DEFAULT_REPLY_TEMPLATE,
        force_error: str = "",
        model_name: str = "mock-model",
    ) -> None:
        self.chunk_size = max(1, chunk_size)
        self.stream_delay_ms = max(0, stream_delay_ms)
        self.reply_template = reply_template
        self.force_error = force_error.strip().lower()
        self.model_name = model_name

    # --- 内部 ---

    def _error_for(self, messages: Sequence[ChatMessage]) -> LLMError | None:
        """决定这次调用要不要抛错。全局开关优先于消息里的触发标记。"""
        if self.force_error:
            error_cls = FORCE_ERROR_MAP.get(self.force_error)
            if error_cls is None:
                return LLMUpstreamError(
                    f"MOCK_FORCE_ERROR 取值非法：{self.force_error!r}，"
                    f"可用值：{sorted(FORCE_ERROR_MAP)}"
                )
            return error_cls(f"mock：按 MOCK_FORCE_ERROR={self.force_error} 强制触发的错误")

        content = _last_user_content(messages)
        if TRIGGER_AUTH in content:
            return LLMAuthError("mock：模拟鉴权失败")
        if TRIGGER_TIMEOUT in content:
            return LLMTimeoutError("mock：模拟请求超时")
        if TRIGGER_RATE_LIMIT in content:
            return LLMRateLimitError("mock：模拟被限流")
        if TRIGGER_UPSTREAM in content:
            return LLMUpstreamError("mock：模拟上游服务错误")
        return None

    def _mid_stream_error(self, messages: Sequence[ChatMessage]) -> LLMError | None:
        if TRIGGER_MID_STREAM in _last_user_content(messages):
            return LLMUpstreamError("mock：模拟流式回答进行到一半时上游断开")
        return None

    def _build_reply(self, messages: Sequence[ChatMessage]) -> str:
        try:
            return self.reply_template.format(message_count=len(messages))
        except (KeyError, IndexError, ValueError):
            # 用户自定义 MOCK_DEFAULT_REPLY 时可能带了别的占位符，不要因此炸掉。
            return self.reply_template

    @staticmethod
    def _tool_markers(messages: Sequence[ChatMessage]) -> list[ToolCall]:
        markers: list[ToolCall] = []
        for line in _last_user_content(messages).splitlines():
            match = _TOOL_MARKER.search(line)
            if not match:
                continue
            arguments = (match.group(2) or "").strip() or "{}"
            markers.append(ToolCall(id="", name=match.group(1), arguments=arguments))
        return markers

    @staticmethod
    def _tool_messages_after_last_user(messages: Sequence[ChatMessage]) -> int:
        last_user = -1
        for index, message in enumerate(messages):
            if message.role == ROLE_USER:
                last_user = index
        return sum(
            1 for message in messages[last_user + 1 :] if message.role == ROLE_TOOL
        )

    def _pending_tool_call(
        self, messages: Sequence[ChatMessage], options: ChatOptions | None
    ) -> ToolCall | None:
        """按"已回填的 tool 消息数"决定本次该发第几个标记，实现确定性多步。"""
        if options is None or not options.tools:
            return None
        markers = self._tool_markers(messages)
        if not markers:
            return None
        done = self._tool_messages_after_last_user(messages)
        if done >= len(markers):
            return None
        marker = markers[done]
        return ToolCall(
            id=f"mock_call_{done}_{len(markers)}",
            name=marker.name,
            arguments=marker.arguments,
        )

    def _chunks(self, text: str) -> list[str]:
        return [text[i : i + self.chunk_size] for i in range(0, len(text), self.chunk_size)]

    async def _sleep_between_chunks(self) -> None:
        if self.stream_delay_ms:
            await asyncio.sleep(self.stream_delay_ms / 1000)

    # --- LLMProvider ---

    async def chat(
        self, messages: Sequence[ChatMessage], options: ChatOptions | None = None
    ) -> ChatResult:
        error = self._error_for(messages)
        if error is not None:
            raise error
        call = self._pending_tool_call(messages, options)
        if call is not None:
            return ChatResult(
                text="",
                model_name=self.model_name,
                finish_reason="tool_calls",
                usage=None,
                tool_calls=(call,),
            )
        return ChatResult(
            text=self._build_reply(messages),
            model_name=self.model_name,
            finish_reason="stop",
            # 不伪造用量：mock 没有真实 token 计数。
            usage=None,
        )

    async def stream_chat(
        self, messages: Sequence[ChatMessage], options: ChatOptions | None = None
    ) -> AsyncIterator[StreamEvent]:
        error = self._error_for(messages)
        if error is not None:
            raise error

        call = self._pending_tool_call(messages, options)
        if call is not None:
            yield StreamEvent.tool_call_event(call)
            yield StreamEvent.done("tool_calls")
            return

        mid_stream_error = self._mid_stream_error(messages)
        reply = self._build_reply(messages)
        chunks = self._chunks(reply)

        for index, chunk in enumerate(chunks):
            if mid_stream_error is not None and index == MID_STREAM_TOKENS_BEFORE_ERROR:
                raise mid_stream_error
            await self._sleep_between_chunks()
            yield StreamEvent.token(chunk)

        yield StreamEvent.done("stop")
