"""OpenAI 兼容协议 adapter（DeepSeek 及同类服务都走这条路）。

刻意用 httpx 手写而不用 openai SDK：依赖已经在，SSE 解析是显式的，出问题时
能直接看到原始数据流。代价是这里要自己处理分块和边界情况。

两个容易踩的点：
1. **每个请求都新建 AsyncClient**。绝不复用长生命周期的 client——用户点
   「停止生成」时，生成器会被关闭，靠 `async with` 的清理来断开上游连接。
   共享 client 会让取消失效，上游还在烧 token。
2. **usage 的位置因供应商而异**。OpenAI 会额外发一个 choices 为空的
   usage chunk；DeepSeek 不发，用量挂在最后一个有内容的 chunk 上。
   所以每个 chunk 都要检查 usage 字段，并且要守卫空 choices。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.ai.providers.base import (
    Capability,
    ChatMessage,
    ChatOptions,
    ChatResult,
    ChatUsage,
    LLMProvider,
    StreamEvent,
)
from app.core.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)

logger = logging.getLogger(__name__)

CHAT_COMPLETIONS_PATH = "/chat/completions"
MODELS_PATH = "/models"
# 日志里最多保留多少字符的上游响应正文。
MAX_LOGGED_BODY_CHARS = 500


class OpenAICompatibleProvider(LLMProvider):
    _capabilities = frozenset({Capability.TEXT_CHAT, Capability.STREAMING})

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        default_max_output_tokens: int = 2048,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAICompatibleProvider 需要 api_key")
        if not model:
            raise ValueError("OpenAICompatibleProvider 需要 model")
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.default_max_output_tokens = default_max_output_tokens
        # 测试时注入 httpx.MockTransport，从而不联网。
        self._transport = transport

    # --- 内部 ---

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout_seconds),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            transport=self._transport,
        )

    def _build_payload(
        self,
        messages: Sequence[ChatMessage],
        options: ChatOptions | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        options = options or ChatOptions()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": options.max_output_tokens or self.default_max_output_tokens,
        }
        if options.temperature is not None:
            payload["temperature"] = options.temperature
        if stream:
            payload["stream"] = True
            # 拿不到用量时下面的解析会给出 usage=None，不会伪造。
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _map_status_error(self, status_code: int, body: bytes) -> LLMError:
        text = body.decode("utf-8", errors="replace")[:MAX_LOGGED_BODY_CHARS]
        # 上游正文只进日志，不进响应体。
        logger.warning("模型服务返回 HTTP %s：%s", status_code, text)
        if status_code in (401, 403):
            return LLMAuthError("模型服务鉴权失败，请检查后端配置的 API Key")
        if status_code == 429:
            return LLMRateLimitError()
        if status_code == 404:
            return LLMUpstreamError(
                f"模型服务返回 404，请确认 LLM_BASE_URL 与 LLM_MODEL 是否正确"
                f"（当前模型：{self.model}）"
            )
        return LLMUpstreamError(f"模型服务返回 HTTP {status_code}")

    @staticmethod
    def _parse_usage(raw: Any) -> ChatUsage | None:
        if not isinstance(raw, dict):
            return None
        return ChatUsage(
            input_tokens=raw.get("prompt_tokens"),
            output_tokens=raw.get("completion_tokens"),
        )

    def _parse_chunk(self, data: str) -> tuple[str | None, str | None, ChatUsage | None]:
        """把一条 SSE data 解析成 (文本增量, finish_reason, usage)。"""
        payload = json.loads(data)
        usage = self._parse_usage(payload.get("usage"))
        choices = payload.get("choices") or []
        if not choices:
            # OpenAI 的 usage-only chunk 就是这个形状：choices 为空。
            return None, None, usage
        choice = choices[0] or {}
        delta = choice.get("delta") or {}
        return delta.get("content"), choice.get("finish_reason"), usage

    # --- LLMProvider ---

    async def chat(
        self, messages: Sequence[ChatMessage], options: ChatOptions | None = None
    ) -> ChatResult:
        payload = self._build_payload(messages, options, stream=False)
        try:
            async with self._client() as client:
                response = await client.post(CHAT_COMPLETIONS_PATH, json=payload)
                if response.status_code >= 400:
                    raise self._map_status_error(response.status_code, response.content)
                data = response.json()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError() from exc
        except httpx.RequestError as exc:
            raise LLMUpstreamError("无法连接模型服务") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMUpstreamError("模型服务返回了空结果")
        choice = choices[0] or {}
        message = choice.get("message") or {}
        return ChatResult(
            text=message.get("content") or "",
            model_name=data.get("model") or self.model,
            finish_reason=choice.get("finish_reason"),
            usage=self._parse_usage(data.get("usage")),
        )

    async def stream_chat(
        self, messages: Sequence[ChatMessage], options: ChatOptions | None = None
    ) -> AsyncIterator[StreamEvent]:
        payload = self._build_payload(messages, options, stream=True)
        finish_reason: str | None = None
        try:
            async with self._client() as client:
                async with client.stream("POST", CHAT_COMPLETIONS_PATH, json=payload) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise self._map_status_error(response.status_code, body)

                    async for line in response.aiter_lines():
                        line = line.strip()
                        # 空行是事件分隔符；': ' 开头是保活注释（heartbeat）。
                        if not line or line.startswith(":"):
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if data == "[DONE]":
                            break
                        try:
                            text, chunk_finish, usage = self._parse_chunk(data)
                        except json.JSONDecodeError:
                            logger.warning("跳过无法解析的 SSE 数据行：%s", data[:200])
                            continue

                        if usage is not None:
                            yield StreamEvent.usage_event(usage)
                        if text:
                            yield StreamEvent.token(text)
                        if chunk_finish:
                            # 记下但**不要立刻 return**：OpenAI 会在带 finish_reason
                            # 的 chunk 之后**再**发一个 choices 为空、只带 usage 的
                            # chunk。提前返回会把用量丢掉。
                            # DeepSeek 则是把 finish_reason 和 usage 放在同一个
                            # chunk 上，两种形态这样处理都能覆盖。
                            finish_reason = chunk_finish
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError() from exc
        except httpx.RequestError as exc:
            raise LLMUpstreamError("无法连接模型服务") from exc

        # 上游没给 finish_reason 就结束了（直接断流），按 stop 处理。
        yield StreamEvent.done(finish_reason or "stop")

    async def list_models(self) -> list[str]:
        """拉取可用模型名，用于确认 LLM_MODEL 该填什么。"""
        try:
            async with self._client() as client:
                response = await client.get(MODELS_PATH)
                if response.status_code >= 400:
                    raise self._map_status_error(response.status_code, response.content)
                data = response.json()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError() from exc
        except httpx.RequestError as exc:
            raise LLMUpstreamError("无法连接模型服务") from exc
        return [item.get("id", "") for item in data.get("data") or [] if item.get("id")]
