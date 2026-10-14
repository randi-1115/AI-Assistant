"""聊天请求契约，以及 SSE 事件的载荷结构。

SSE 事件名和字段与 docs/API-SPEC.md 一一对应；前端 types/chat.ts 是它的镜像，
两边改动必须同步。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.ai.providers.base import ChatUsage

MAX_MESSAGE_CHARS = 10000


class ChatMode(StrEnum):
    """创作模式。P0 全部按 chat 处理，枚举先冻结契约。"""

    CHAT = "chat"
    WRITE = "write"
    POLISH = "polish"
    SUMMARIZE = "summarize"
    TRANSLATE = "translate"
    BRAINSTORM = "brainstorm"
    STUDY = "study"


class SearchMode(StrEnum):
    AUTO = "auto"
    OFF = "off"


class ChatStreamRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    # P1 才会用到；P0 接受但忽略。
    attachment_ids: list[int] = Field(default_factory=list)
    mode: ChatMode = ChatMode.CHAT
    search_mode: SearchMode = SearchMode.AUTO


class UsagePayload(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class MessageStartPayload(BaseModel):
    user_message_id: int
    assistant_message_id: int


class TokenPayload(BaseModel):
    text: str


class MessageEndPayload(BaseModel):
    finish_reason: str
    usage: UsagePayload | None = None


class SSEErrorPayload(BaseModel):
    code: str
    message: str
    request_id: str


def dump_usage(usage: ChatUsage | None) -> dict[str, int | None] | None:
    """用量拿不到就返回 None——绝不填 0 冒充。"""
    return None if usage is None else usage.as_dict()
