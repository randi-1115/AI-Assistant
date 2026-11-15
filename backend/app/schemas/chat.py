"""聊天请求契约，以及 SSE 事件的载荷结构。

SSE 事件名和字段与 docs/API-SPEC.md 一一对应；前端 types/chat.ts 是它的镜像，
两边改动必须同步。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

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
    # 显式附带的文件；只有这些文件参与本轮检索。
    attachment_ids: list[int] = Field(default_factory=list, max_length=10)
    mode: ChatMode = ChatMode.CHAT
    # 默认关闭：联网搜索会产生外部请求与费用，必须由用户显式开启。
    search_mode: SearchMode = SearchMode.OFF


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


class FileSourcePayload(BaseModel):
    """文件来源。文件没有下载端点，url 明确为 None；不伪造空链接。"""

    kind: Literal["file"] = "file"
    file_id: int
    chunk_index: int
    title: str
    url: Literal[None] = None
    section_title: str | None = None
    snippet: str | None = None
    score: float
    excerpt_number: int | None = None
    citation_label: str | None = None


class WebSourcePayload(BaseModel):
    """联网搜索来源。url 必填，前端渲染为可点击链接。"""

    kind: Literal["web"] = "web"
    title: str
    url: str
    snippet: str | None = None
    published_date: str | None = None
    domain: str | None = None
    score: float | None = None


# SSE source 事件的两种载荷。实际发送的是 dict，保留联合类型供契约参考。
SourcePayload = FileSourcePayload | WebSourcePayload


class ToolStatusPayload(BaseModel):
    """工具执行状态。running 只作为流式瞬态事件，不落库。"""

    tool: Literal["web_search"] = "web_search"
    status: Literal["running", "done", "empty", "unavailable"]
    message: str | None = None
    provider: str | None = None


def dump_usage(usage: ChatUsage | None) -> dict[str, int | None] | None:
    """用量拿不到就返回 None——绝不填 0 冒充。"""
    return None if usage is None else usage.as_dict()
