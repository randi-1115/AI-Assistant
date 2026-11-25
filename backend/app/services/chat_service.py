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
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.prompts.system import AGENT_TOOL_PROMPT, TITLE_SYSTEM_PROMPT, get_system_prompt
from app.ai.providers.base import ChatMessage, ChatOptions, LLMProvider, ROLE_USER
from app.core.config import Settings, get_settings
from app.core.errors import (
    AppError,
    ConversationNotFound,
    FileNotFound,
    LLMError,
    TooManyAttachments,
    ValidationAppError,
)
from app.core.logging import get_request_id
from app.db.session import SessionLocal
from app.models.attachment import PARSE_READY, Attachment
from app.models.conversation import DEFAULT_CONVERSATION_TITLE
from app.rag.pipeline import (
    build_excerpts,
    build_rag_block,
    build_source_payloads,
    validate_citations,
)
from app.rag.retriever import KeywordRetriever, query_terms
from app.repositories.attachment_repo import AttachmentRepository
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
from app.search.pipeline import (
    build_web_block,
    build_web_empty_block,
    build_web_source_payloads,
    build_web_unavailable_block,
    merge_web_context,
)
from app.search.service import SearchOutcome, SearchService
from app.services.agent_service import AgentService, AgentTurnContext, TurnState
from app.services.context_builder import build_context
from app.services.file_service import attachment_snapshot

logger = logging.getLogger(__name__)

# 自动标题最多保留多少字。太长的标题在侧边栏里会被截断。
MAX_TITLE_CHARS = 20
# 取历史时最多捞多少条，超过上下文预算的部分由 build_context 裁掉。
HISTORY_FETCH_LIMIT = 200


MAX_ATTACHMENT_IDS = 10

# 两个执行器（P1 预检索 / P2 工具循环）共用的 SSE 事件形状。
ChatEvent = tuple[str, dict[str, Any]]


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
    # 工具上下文需要，agent 模式下由 AgentService 使用。
    user_id: int = 0
    # 本轮附带的文件 id；agent 模式下作为 document_search 的硬边界。
    attachment_ids: list[int] = field(default_factory=list)
    # 本轮检索到的文件来源，流开始后逐条通过 SSE 发给前端。
    sources: list[dict[str, Any]] = field(default_factory=list)
    # 重新生成时从用户消息继承的设置与原始问题。
    query: str = ""
    search_mode: str = "off"
    agent_mode: str = "off"


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


def _normalize_agent_mode(raw: str, settings: Settings) -> str:
    """没有启用任何工具时，agent 模式没有意义，直接按 off 处理。"""
    return "auto" if raw == "auto" and settings.tools_enabled_names else "off"


def _compose_system_prompt(
    *,
    rag_block: str | None,
    agent_mode: str,
    attachment_count: int,
) -> str:
    system_prompt = get_system_prompt("chat")
    if rag_block:
        system_prompt = f"{system_prompt}\n\n{rag_block}"
    if agent_mode == "auto":
        system_prompt = f"{system_prompt}\n\n{AGENT_TOOL_PROMPT}"
        if attachment_count:
            system_prompt += (
                f"\n\n本轮消息附带了 {attachment_count} 个文件；"
                "需要文件内容时请调用 document_search 工具，"
                "不要凭空假设文件内容。"
            )
    return system_prompt


def _context_budget(
    settings: Settings,
    system_prompt: str,
    *,
    search_mode: str,
    agent_mode: str,
) -> int:
    budget = settings.context_max_chars - len(system_prompt)
    # agent 模式不预检索网页，工具结果在循环中产生，不需要预留搜索预算。
    if search_mode == "auto" and agent_mode != "auto":
        budget -= settings.search_context_max_chars
    return max(0, budget)


class ChatService:
    def __init__(
        self,
        provider: LLMProvider,
        session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
        options_factory: Callable[[], ChatOptions] | None = None,
        search_service: SearchService | None = None,
    ) -> None:
        self.provider = provider
        self.session_factory = session_factory
        self._options_factory = options_factory or self._default_options
        self.search_service = search_service or SearchService(get_settings())
        self.agent_service = AgentService(
            provider,
            self.search_service,
            get_settings(),
            session_factory=session_factory,
            options_factory=self._options_factory,
        )

    @staticmethod
    def _default_options() -> ChatOptions:
        settings = get_settings()
        return ChatOptions(
            temperature=settings.llm_temperature,
            max_output_tokens=settings.llm_max_output_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    # --- 准备阶段（错误在这里抛出，前端收到的是普通 JSON 错误）---

    async def _prepare_rag(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        user_id: int,
        conversation_id: int,
        query: str,
        attachment_ids: list[int] | None,
        strict: bool,
        retrieve: bool = True,
    ) -> tuple[list[Attachment], list[dict[str, Any]], str | None]:
        """校验附件、检索片段、组装提示词块。

        strict=True（新消息）：不存在/别人的/跨会话的附件直接 404。
        strict=False（重新生成）：跳过已删除或已解绑的附件，不让历史附件
        阻止重新生成。
        retrieve=False（agent 模式）：仍然校验/绑定附件，但跳过检索——
        文件内容改由 document_search 工具按需检索。
        """
        deduped: list[int] = []
        seen: set[int] = set()
        for file_id in attachment_ids or []:
            if file_id in seen:
                continue
            seen.add(file_id)
            deduped.append(file_id)
        if strict and len(deduped) > MAX_ATTACHMENT_IDS:
            raise TooManyAttachments()
        deduped = deduped[:MAX_ATTACHMENT_IDS]
        if not deduped:
            return [], [], None

        repo = AttachmentRepository(session)
        attachments = await repo.list_owned_by_ids(user_id, deduped)
        by_id = {attachment.id: attachment for attachment in attachments}

        if strict:
            missing = [file_id for file_id in deduped if file_id not in by_id]
            if missing:
                raise FileNotFound()

        scoped: list[Attachment] = []
        for file_id in deduped:
            attachment = by_id.get(file_id)
            if attachment is None:
                continue
            if attachment.conversation_id in (None, conversation_id):
                scoped.append(attachment)
            elif strict:
                # 阶段 2 不支持跨会话复用附件；对当前会话来说等同于不存在。
                raise FileNotFound()
        if not scoped:
            return [], [], None

        # 首次使用时绑定到当前会话，删除会话才能级联清理这些文件。
        unclaimed = [attachment.id for attachment in scoped if attachment.conversation_id is None]
        if unclaimed:
            await repo.claim_conversation(user_id, unclaimed, conversation_id)
            for attachment in scoped:
                if attachment.id in unclaimed:
                    attachment.conversation_id = conversation_id

        if not retrieve:
            return scoped, [], None

        ready_ids = [
            attachment.id for attachment in scoped if attachment.parse_status == PARSE_READY
        ]
        retrieved = []
        if ready_ids:
            retrieved = await KeywordRetriever(session, settings).retrieve(
                user_id=user_id,
                query=query,
                attachment_ids=ready_ids,
            )

        terms = query_terms(query)
        excerpts = build_excerpts(
            retrieved,
            terms=terms,
            max_chars=settings.rag_context_max_chars,
            per_file_max_excerpts=settings.rag_max_excerpts_per_file,
        )
        snapshots = [attachment_snapshot(attachment) for attachment in scoped]
        rag_block = build_rag_block(excerpts, attachments=snapshots)
        sources = build_source_payloads(
            excerpts,
            terms=terms,
            snippet_chars=settings.rag_source_snippet_chars,
        )
        return scoped, sources, rag_block

    async def prepare_send(
        self,
        *,
        conversation_id: int,
        user_id: int,
        content: str,
        attachment_ids: list[int] | None = None,
        search_mode: str = "off",
        agent_mode: str = "off",
    ) -> PreparedTurn:
        settings = get_settings()
        search_mode = "auto" if search_mode == "auto" else "off"
        agent_mode = _normalize_agent_mode(agent_mode, settings)
        async with self.session_factory() as session:
            conversations = ConversationRepository(session)
            conversation = await conversations.get_owned(conversation_id, user_id)
            if conversation is None:
                raise ConversationNotFound()

            # 先检索，再写消息：检索失败不会留下孤儿消息。
            # agent 模式下文件内容交给 document_search 工具按需检索。
            attachments, sources, rag_block = await self._prepare_rag(
                session=session,
                settings=settings,
                user_id=user_id,
                conversation_id=conversation_id,
                query=content,
                attachment_ids=attachment_ids,
                strict=True,
                retrieve=agent_mode != "auto",
            )

            messages = MessageRepository(session)
            # 插入之前先看一眼有没有历史，用来判断是不是首轮。
            existing = await messages.list_recent(conversation_id, limit=1)
            is_first_turn = not existing
            title_is_default = conversation.title == DEFAULT_CONVERSATION_TITLE

            user_metadata: dict[str, Any] = {
                "search_mode": search_mode,
                "agent_mode": agent_mode,
            }
            if attachments:
                user_metadata["attachments"] = [
                    attachment_snapshot(item) for item in attachments
                ]
            user_message = await messages.create(
                conversation_id,
                role=MSG_ROLE_USER,
                content=content,
                status=STATUS_COMPLETE,
                metadata_json=user_metadata,
            )
            assistant_message = await messages.create(
                conversation_id, role=ROLE_ASSISTANT, content="", status=STATUS_STREAMING
            )
            await conversations.touch(conversation_id)

            history = await messages.list_recent(conversation_id, limit=HISTORY_FETCH_LIMIT)
            await session.commit()

        system_prompt = _compose_system_prompt(
            rag_block=rag_block,
            agent_mode=agent_mode,
            attachment_count=len(attachments),
        )
        provider_messages = build_context(
            history,
            system_prompt=system_prompt,
            max_messages=settings.context_max_messages,
            max_chars=_context_budget(
                settings,
                system_prompt,
                search_mode=search_mode,
                agent_mode=agent_mode,
            ),
        )
        return PreparedTurn(
            conversation_id=conversation_id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            provider_messages=provider_messages,
            is_first_turn=is_first_turn,
            first_user_content=content,
            title_is_default=title_is_default,
            user_id=user_id,
            attachment_ids=[attachment.id for attachment in attachments],
            sources=sources,
            query=content,
            search_mode=search_mode,
            agent_mode=agent_mode,
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

            # 重新生成复用上一条用户消息附带的文件快照；已删除或已解绑的
            # 附件在 strict=False 下直接跳过，不阻断重新生成。
            raw_attachments = (last_user.metadata_json or {}).get("attachments", [])
            attachment_ids = [
                item.get("id")
                for item in raw_attachments
                if isinstance(item, dict) and isinstance(item.get("id"), int)
            ]
            raw_mode = (last_user.metadata_json or {}).get("search_mode", "off")
            search_mode = "auto" if raw_mode == "auto" else "off"
            raw_agent = (last_user.metadata_json or {}).get("agent_mode", "off")
            agent_mode = _normalize_agent_mode(raw_agent, settings)
            scoped, sources, rag_block = await self._prepare_rag(
                session=session,
                settings=settings,
                user_id=user_id,
                conversation_id=conversation_id,
                query=last_user.content or "",
                attachment_ids=attachment_ids,
                strict=False,
                retrieve=agent_mode != "auto",
            )

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

        system_prompt = _compose_system_prompt(
            rag_block=rag_block,
            agent_mode=agent_mode,
            attachment_count=len(scoped),
        )
        provider_messages = build_context(
            history,
            system_prompt=system_prompt,
            max_messages=settings.context_max_messages,
            max_chars=_context_budget(
                settings,
                system_prompt,
                search_mode=search_mode,
                agent_mode=agent_mode,
            ),
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
            user_id=user_id,
            attachment_ids=[attachment.id for attachment in scoped],
            sources=sources,
            query=last_user.content or "",
            search_mode=search_mode,
            agent_mode=agent_mode,
        )

    # --- 流式执行 ---

    async def _search(self, query: str) -> SearchOutcome:
        """联网搜索永远不能把异常抛进聊天流。"""
        try:
            return await self.search_service.search(query, mode="auto")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("联网搜索出现未预期异常")
            return SearchOutcome(status="unavailable", message="联网搜索暂时不可用")

    async def run_turn(
        self, prepared: PreparedTurn, request: Request
    ) -> AsyncIterator[ChatEvent]:
        settings = get_settings()
        state = TurnState()
        state.seed_sources(prepared.sources)

        try:
            yield "message_start", {
                "user_message_id": prepared.user_message_id,
                "assistant_message_id": prepared.assistant_message_id,
            }

            # 两个模式共用同一个执行契约：只产出 token/tool_status/source，
            # 状态写进 TurnState；message_start/message_end、引用校验与落库
            # 都由 run_turn 统一负责。
            executor = self._run_agent if prepared.agent_mode == "auto" else self._run_p1
            async for item in executor(state, prepared, request):
                yield item

            if state.status == STATUS_COMPLETE:
                if settings.rag_citation_validation and state.sources:
                    state.citation_report = validate_citations(
                        "".join(state.chunks), state.sources
                    )
                state.finish_reason = state.finish_reason or "stop"
                yield "message_end", {
                    "finish_reason": state.finish_reason,
                    "usage": dump_usage(state.usage),
                }

        except asyncio.CancelledError:
            # 兜底路径：客户端硬断（关标签页）时 Starlette 会取消这个任务。
            state.status = STATUS_INTERRUPTED
            state.finish_reason = "interrupted"
            await self._finalize_safely(prepared, state)
            # 必须重新抛出——吞掉 CancelledError 会破坏 ASGI 的取消语义。
            raise

        except (LLMError, AppError) as exc:
            state.status = STATUS_FAILED
            state.finish_reason = "error"
            logger.warning(
                "流式回答失败 conversation_id=%s code=%s", prepared.conversation_id, exc.code
            )
            # 错误信封与普通 JSON 错误保持同一结构，前端只需一套解析逻辑。
            yield "error", {
                "code": exc.code,
                "message": exc.message,
                "request_id": get_request_id(),
            }
            await self._finalize_safely(prepared, state)
            yield "message_end", {
                "finish_reason": "error",
                "usage": dump_usage(state.usage),
            }
            return

        except Exception:
            state.status = STATUS_FAILED
            state.finish_reason = "error"
            logger.exception(
                "流式回答出现未预期异常 conversation_id=%s", prepared.conversation_id
            )
            yield "error", {
                "code": "internal_error",
                "message": "服务器内部错误",
                "request_id": get_request_id(),
            }
            await self._finalize_safely(prepared, state)
            yield "message_end", {
                "finish_reason": "error",
                "usage": dump_usage(state.usage),
            }
            return

        await self._finalize_safely(prepared, state)

    async def _run_agent(
        self, state: TurnState, prepared: PreparedTurn, request: Request
    ) -> AsyncIterator["ChatEvent"]:
        """P2 执行器：把本轮交给 AgentService 的工具循环。"""
        context = AgentTurnContext(
            conversation_id=prepared.conversation_id,
            assistant_message_id=prepared.assistant_message_id,
            user_id=prepared.user_id,
            attachment_ids=tuple(prepared.attachment_ids),
            search_mode=prepared.search_mode,
            request_id=get_request_id(),
        )
        async for item in self.agent_service.run_turn(
            state,
            context,
            prepared.provider_messages,
            request,
            self._options_factory(),
        ):
            yield item

    async def _run_p1(
        self,
        state: TurnState,
        prepared: PreparedTurn,
        request: Request,
    ) -> AsyncIterator["ChatEvent"]:
        """P1 预检索执行器：顺序必须与既有契约一致。

        事件顺序：running tool_status → 文件来源 → 网页来源 → 终态 → token。
        与 _run_agent 一样，不负责 message_start / message_end，也不写库。
        """
        settings = get_settings()
        if prepared.search_mode == "auto":
            # 搜索是外部调用，先让前端知道正在做什么。
            yield "tool_status", {"tool": "web_search", "status": "running"}

        # 文件来源已经准备好，先发；web 来源在搜索成功后追加。
        for source in prepared.sources:
            yield "source", source

        provider_messages = prepared.provider_messages
        if prepared.search_mode == "auto":
            outcome = await self._search(prepared.query)
            extra_prompt: str | None
            if outcome.status == "ok":
                web_sources = build_web_source_payloads(
                    outcome.results,
                    snippet_chars=settings.search_snippet_chars,
                )
                state.sources.extend(web_sources)
                for payload in web_sources:
                    yield "source", payload
                extra_prompt = build_web_block(
                    outcome.results,
                    max_chars=settings.search_context_max_chars,
                    intent=outcome.intent,
                )
            elif outcome.status == "empty":
                extra_prompt = build_web_empty_block(outcome.message)
            else:
                extra_prompt = build_web_unavailable_block(outcome.message)

            terminal = {
                "tool": "web_search",
                "status": {"ok": "done", "empty": "empty"}.get(
                    outcome.status, "unavailable"
                ),
                "message": outcome.message,
                "provider": outcome.provider,
            }
            state.tool_statuses.append(terminal)
            yield "tool_status", terminal

            # 搜索期间用户可能已经点了停止；此时不再调用模型。
            if await request.is_disconnected():
                state.status = STATUS_INTERRUPTED
                state.finish_reason = "interrupted"
                return

            provider_messages = merge_web_context(
                prepared.provider_messages, extra_prompt
            )

        async for event in self.provider.stream_chat(
            provider_messages, self._options_factory()
        ):
            # 主动检查断开：前端点「停止生成」后，这里是最先能感知到的地方。
            # 走这条路不抛异常，能干净地收尾。
            if await request.is_disconnected():
                state.status = STATUS_INTERRUPTED
                state.finish_reason = "interrupted"
                return

            if event.type == "token" and event.text:
                state.chunks.append(event.text)
                yield "token", {"text": event.text}
            elif event.type == "usage":
                state.usage = event.usage
            elif event.type == "done":
                state.finish_reason = event.finish_reason or "stop"

    async def _finalize_safely(
        self,
        prepared: PreparedTurn,
        state: TurnState,
    ) -> None:
        """回填助手消息。用独立 session，且不让写库失败影响流的收尾。"""
        metadata: dict = {}
        if state.finish_reason:
            metadata["finish_reason"] = state.finish_reason
        if state.usage is not None:
            metadata["usage"] = state.usage.as_dict()
        # 来源在失败/中断时也保留，刷新历史后仍能看到引用过哪些片段。
        if state.sources:
            metadata["sources"] = list(state.sources)
        # running 是瞬态状态，不落库；只保存终态。
        if state.tool_statuses:
            metadata["tool_statuses"] = list(state.tool_statuses)
        if state.citation_report:
            metadata["citation_report"] = state.citation_report
        if prepared.agent_mode == "auto":
            metadata["agent"] = {
                "steps": state.steps,
                "tool_calls": state.tool_calls,
                "limit_reached": state.limit_reached,
            }

        async def write() -> None:
            async with self.session_factory() as session:
                await MessageRepository(session).finalize(
                    prepared.assistant_message_id,
                    content="".join(state.chunks),
                    status=state.status,
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
