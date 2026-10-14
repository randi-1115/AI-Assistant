"""聊天主流程：组装上下文、跑流式回答、落库、取消处理、自动标题。

这是全项目最容易写错的文件，几个约束值得先说明：

1. **先落库、再开流**。用户消息和助手占位消息在第一个 SSE 事件之前就写好了，
   所以前端拿到 message_start 里的 id 时，数据库里已经有对应记录。
   会话不存在这类错误也因此能在开流前抛出，走标准 JSON 错误响应。

2. **流一旦开始，就不能再返回 JSON 错误**。HTTP 头和状态码已经发出去了，
   只能发 `event: error`。这由 run_turn 内部捕获异常来处理。

3. **落库用独立的 session**。请求注入的 session 在客户端断开时可能已经被
   拆解，所以 finalize 一律新建 SessionLocal()。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.prompts.system import TITLE_SYSTEM_PROMPT, get_system_prompt
from app.ai.providers.base import ChatMessage, ChatOptions, ChatUsage, LLMProvider, ROLE_USER
from app.core.config import get_settings
from app.core.errors import (
    AppError,
    ConversationNotFound,
    LLMError,
    ValidationAppError,
)
from app.core.logging import get_request_id
from app.db.session import SessionLocal
from app.models.conversation import DEFAULT_CONVERSATION_TITLE
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import (
    ROLE_ASSISTANT,
    ROLE_USER as MSG_ROLE_USER,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_STREAMING,
    MessageRepository,
)
from app.schemas.chat import dump_usage
from app.services.context_builder import build_context

logger = logging.getLogger(__name__)

# 自动标题最多保留多少字。太长的标题在侧边栏里会被截断。
MAX_TITLE_CHARS = 20
# 取历史时最多捞多少条，超过上下文预算的部分由 build_context 裁掉。
HISTORY_FETCH_LIMIT = 200


@dataclass(slots=True)
class PreparedTurn:
    conversation_id: int
    user_message_id: int
    assistant_message_id: int
    provider_messages: list[ChatMessage]
    # 首轮对话结束后会自动生成标题；后续轮次不动标题。
    is_first_turn: bool
    first_user_content: str
    title_is_default: bool


def fallback_title(content: str) -> str:
    """模型起标题失败时的降级方案：截断首句。"""
    normalized = " ".join(content.split())
    if not normalized:
        return DEFAULT_CONVERSATION_TITLE
    if len(normalized) <= MAX_TITLE_CHARS:
        return normalized
    return normalized[:MAX_TITLE_CHARS]


def clean_generated_title(raw: str) -> str:
    """模型有时会带上引号、书名号或换行，这里统一清洗。"""
    title = " ".join(raw.split()).strip()
    title = title.strip("「」『』\"'“”‘’《》.。,，:：;；")
    return title[:MAX_TITLE_CHARS]


class ChatService:
    def __init__(
        self,
        provider: LLMProvider,
        session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
        options_factory: Callable[[], ChatOptions] | None = None,
    ) -> None:
        self.provider = provider
        self.session_factory = session_factory
        self._options_factory = options_factory or self._default_options

    @staticmethod
    def _default_options() -> ChatOptions:
        settings = get_settings()
        return ChatOptions(
            temperature=settings.llm_temperature,
            max_output_tokens=settings.llm_max_output_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    # --- 准备阶段（错误在这里抛出，前端收到的是普通 JSON 错误）---

    async def prepare_send(
        self, *, conversation_id: int, user_id: int, content: str
    ) -> PreparedTurn:
        settings = get_settings()
        async with self.session_factory() as session:
            conversations = ConversationRepository(session)
            conversation = await conversations.get_owned(conversation_id, user_id)
            if conversation is None:
                raise ConversationNotFound()

            messages = MessageRepository(session)
            # 插入之前先看一眼有没有历史，用来判断是不是首轮。
            existing = await messages.list_recent(conversation_id, limit=1)
            is_first_turn = not existing
            title_is_default = conversation.title == DEFAULT_CONVERSATION_TITLE

            user_message = await messages.create(
                conversation_id, role=MSG_ROLE_USER, content=content, status=STATUS_COMPLETE
            )
            assistant_message = await messages.create(
                conversation_id, role=ROLE_ASSISTANT, content="", status=STATUS_STREAMING
            )
            await conversations.touch(conversation_id)

            history = await messages.list_recent(conversation_id, limit=HISTORY_FETCH_LIMIT)
            await session.commit()

        provider_messages = build_context(
            history,
            system_prompt=get_system_prompt("chat"),
            max_messages=settings.context_max_messages,
            max_chars=settings.context_max_chars,
        )
        return PreparedTurn(
            conversation_id=conversation_id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            provider_messages=provider_messages,
            is_first_turn=is_first_turn,
            first_user_content=content,
            title_is_default=title_is_default,
        )

    async def prepare_regenerate(self, *, conversation_id: int, user_id: int) -> PreparedTurn:
        settings = get_settings()
        async with self.session_factory() as session:
            conversations = ConversationRepository(session)
            conversation = await conversations.get_owned(conversation_id, user_id)
            if conversation is None:
                raise ConversationNotFound()

            messages = MessageRepository(session)
            previous = await messages.last_assistant(conversation_id)
            if previous is None:
                raise ValidationAppError("这个会话还没有可重新生成的回答")
            last_user = await messages.last_user(conversation_id)
            if last_user is None:
                raise ValidationAppError("这个会话还没有用户提问")

            # 硬删旧的助手回答：保持 messages.id 单调，也避免多轮重新生成后
            # 残留一堆被覆盖的历史回答。上下文用「截至最后一条用户消息」组装，
            # 因此自然不含被删掉的那条。
            await messages.delete_by_id(previous.id)
            assistant_message = await messages.create(
                conversation_id, role=ROLE_ASSISTANT, content="", status=STATUS_STREAMING
            )
            await conversations.touch(conversation_id)

            history = await messages.list_recent(
                conversation_id, limit=HISTORY_FETCH_LIMIT, max_id=last_user.id
            )
            await session.commit()

        provider_messages = build_context(
            history,
            system_prompt=get_system_prompt("chat"),
            max_messages=settings.context_max_messages,
            max_chars=settings.context_max_chars,
        )
        return PreparedTurn(
            conversation_id=conversation_id,
            user_message_id=last_user.id,
            assistant_message_id=assistant_message.id,
            provider_messages=provider_messages,
            # 重新生成不改标题。
            is_first_turn=False,
            first_user_content=last_user.content,
            title_is_default=False,
        )

    # --- 流式执行 ---

    async def run_turn(
        self, prepared: PreparedTurn, request: Request
    ) -> AsyncIterator[tuple[str, dict]]:
        chunks: list[str] = []
        usage: ChatUsage | None = None
        finish_reason: str | None = None
        status = STATUS_COMPLETE

        try:
            yield "message_start", {
                "user_message_id": prepared.user_message_id,
                "assistant_message_id": prepared.assistant_message_id,
            }

            async for event in self.provider.stream_chat(
                prepared.provider_messages, self._options_factory()
            ):
                # 主动检查断开：前端点「停止生成」后，这里是最先能感知到的地方。
                # 走这条路不抛异常，能干净地收尾。
                if await request.is_disconnected():
                    status = STATUS_INTERRUPTED
                    finish_reason = "interrupted"
                    break

                if event.type == "token" and event.text:
                    chunks.append(event.text)
                    yield "token", {"text": event.text}
                elif event.type == "usage":
                    usage = event.usage
                elif event.type == "done":
                    finish_reason = event.finish_reason or "stop"

            if status == STATUS_COMPLETE:
                finish_reason = finish_reason or "stop"
                yield "message_end", {
                    "finish_reason": finish_reason,
                    "usage": dump_usage(usage),
                }

        except asyncio.CancelledError:
            # 兜底路径：客户端硬断（关标签页）时 Starlette 会取消这个任务。
            await self._finalize_safely(
                prepared, "".join(chunks), STATUS_INTERRUPTED, usage, "interrupted"
            )
            # 必须重新抛出——吞掉 CancelledError 会破坏 ASGI 的取消语义。
            raise

        except (LLMError, AppError) as exc:
            status = STATUS_FAILED
            finish_reason = "error"
            logger.warning("流式回答失败 conversation_id=%s code=%s", prepared.conversation_id, exc.code)
            # 错误信封与普通 JSON 错误保持同一结构，前端只需一套解析逻辑。
            yield "error", {
                "code": exc.code,
                "message": exc.message,
                "request_id": get_request_id(),
            }
            await self._finalize_safely(
                prepared, "".join(chunks), STATUS_FAILED, usage, "error"
            )
            yield "message_end", {"finish_reason": "error", "usage": dump_usage(usage)}
            return

        except Exception:
            status = STATUS_FAILED
            finish_reason = "error"
            logger.exception("流式回答出现未预期异常 conversation_id=%s", prepared.conversation_id)
            yield "error", {
                "code": "internal_error",
                "message": "服务器内部错误",
                "request_id": get_request_id(),
            }
            await self._finalize_safely(
                prepared, "".join(chunks), STATUS_FAILED, usage, "error"
            )
            yield "message_end", {"finish_reason": "error", "usage": dump_usage(usage)}
            return

        await self._finalize_safely(
            prepared, "".join(chunks), status, usage, finish_reason or "stop"
        )

    async def _finalize_safely(
        self,
        prepared: PreparedTurn,
        content: str,
        status: str,
        usage: ChatUsage | None,
        finish_reason: str | None,
    ) -> None:
        """回填助手消息。用独立 session，且不让写库失败影响流的收尾。"""
        metadata: dict = {}
        if finish_reason:
            metadata["finish_reason"] = finish_reason
        if usage is not None:
            metadata["usage"] = usage.as_dict()

        async def write() -> None:
            async with self.session_factory() as session:
                await MessageRepository(session).finalize(
                    prepared.assistant_message_id,
                    content=content,
                    status=status,
                    model_provider=self.provider.name,
                    model_name=getattr(self.provider, "model", None),
                    metadata_json=metadata or None,
                )
                await ConversationRepository(session).touch(prepared.conversation_id)
                await session.commit()

        try:
            # shield：即使外层任务正在被取消，也让写库跑完。
            await asyncio.shield(write())
        except asyncio.CancelledError:
            # 这里不能再抛——否则 run_turn 的 except 分支就写不成库了。
            # 极端情况下（进程被杀）会残留 streaming 消息，由启动时的清理兜底。
            logger.info("写库被取消，已交给后台任务完成 message_id=%s", prepared.assistant_message_id)
        except Exception:
            logger.exception("回填助手消息失败 message_id=%s", prepared.assistant_message_id)

    # --- 自动标题 ---

    async def generate_title(self, conversation_id: int, first_user_content: str) -> None:
        """首轮对话后自动起标题。**任何失败都不影响聊天主流程。**"""
        title = fallback_title(first_user_content)
        try:
            result = await self.provider.chat(
                [
                    ChatMessage(role="system", content=TITLE_SYSTEM_PROMPT),
                    ChatMessage(role=ROLE_USER, content=first_user_content),
                ],
                ChatOptions(
                    temperature=0.3,
                    max_output_tokens=64,
                    timeout_seconds=15.0,
                ),
            )
            cleaned = clean_generated_title(result.text or "")
            if cleaned:
                title = cleaned
        except Exception:
            logger.info("自动生成标题失败，降级为截断首句 conversation_id=%s", conversation_id)

        try:
            async with self.session_factory() as session:
                await ConversationRepository(session).set_title_if_default(
                    conversation_id, title, DEFAULT_CONVERSATION_TITLE
                )
                await session.commit()
        except Exception:
            logger.exception("写入自动标题失败 conversation_id=%s", conversation_id)
