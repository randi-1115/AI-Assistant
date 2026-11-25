"""文档检索工具：复用 P1 的关键词 RAG，只检索本轮附带的文件。

作用域是硬边界：允许的文件 = `ToolContext.attachment_ids`（本轮消息的附件）。
`file_ids` 只能在这个集合内**收窄**，不能扩大；越界请求统一返回
`file_scope_denied`，不暴露文件是否存在。

工具是只读的：不绑定附件、不改归属、不删数据。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from app.core.config import Settings
from app.models.attachment import PARSE_FAILED, PARSE_READY, PARSE_UNSUPPORTED, Attachment
from app.rag.pipeline import build_excerpts, build_rag_block, build_source_payloads
from app.rag.retriever import KeywordRetriever, query_terms
from app.repositories.attachment_repo import AttachmentRepository
from app.services.file_service import attachment_snapshot
from app.tools.base import (
    Tool,
    ToolArgs,
    ToolContext,
    ToolError,
    ToolResult,
    ToolUnavailable,
)

NO_SCOPE_MESSAGE = "本轮消息没有附带文件，无法检索资料。请让用户先上传文件并作为附件发送。"
NO_MATCH_MESSAGE = "没有找到与问题直接相关的片段。"


class DocumentSearchArgs(ToolArgs):
    query: str = Field(
        min_length=1,
        max_length=400,
        description="要检索的问题或关键词。",
    )
    file_ids: list[Annotated[int, Field(gt=0)]] | None = Field(
        default=None,
        max_length=10,
        description="可选：只在指定的本轮附件中检索；不能引用本轮之外的文件。",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="可选：最多返回多少个片段，默认沿用服务端 RAG_TOP_K。",
    )


class DocumentSearchTool(Tool[DocumentSearchArgs]):
    name = "document_search"
    description = (
        "在用户本轮消息附带的文件中检索相关片段。"
        "只有当本轮确实附带了文件时才应调用；不能检索会话之外或其他用户的文件。"
    )
    args_model = DocumentSearchArgs

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.max_output_chars = settings.rag_context_max_chars

    async def run(
        self, args: DocumentSearchArgs, context: ToolContext
    ) -> ToolResult:
        allowed = tuple(dict.fromkeys(context.attachment_ids))
        explicitly_scoped = args.file_ids is not None
        wanted = self._resolve_scope(args.file_ids, allowed)

        if not wanted:
            return ToolResult(
                status="empty",
                content=NO_SCOPE_MESSAGE,
                message="本轮没有可检索的文件",
                data={
                    "requested_file_ids": list(wanted),
                    "searched_file_ids": [],
                    "skipped_file_ids": [],
                    "not_ready_file_ids": [],
                    "retrieved_chunks": 0,
                    "excerpt_count": 0,
                },
            )

        session_factory = context.session_factory
        if session_factory is None:
            raise ToolError("工具缺少数据库会话", code="internal_error")

        async with session_factory() as session:
            attachments = await AttachmentRepository(session).list_owned_by_ids(
                context.user_id, wanted
            )
            by_id = {attachment.id: attachment for attachment in attachments}

            scoped: list[Attachment] = []
            skipped: list[int] = []
            for file_id in wanted:
                attachment = by_id.get(file_id)
                if attachment is None or not self._in_conversation(attachment, context):
                    if explicitly_scoped:
                        # 显式点名了本轮范围之外的文件：直接拒绝，不透露存在性。
                        raise ToolError(
                            "只能检索本轮消息附带的文件", code="file_scope_denied"
                        )
                    # 默认范围里的文件可能在准备阶段之后被删除/解绑，跳过即可。
                    skipped.append(file_id)
                    continue
                scoped.append(attachment)

            if not scoped:
                return ToolResult(
                    status="empty",
                    content="之前附带的文件已不可用，没有可检索的资料。",
                    message="没有可检索的文件",
                    data=self._data(wanted, [], skipped, [], [], []),
                )

            ready = [item for item in scoped if item.parse_status == PARSE_READY]
            not_ready = [item for item in scoped if item.parse_status != PARSE_READY]
            if not ready:
                raise self._not_ready_error(not_ready)

            retrieved = await KeywordRetriever(session, self.settings).retrieve(
                user_id=context.user_id,
                query=args.query,
                attachment_ids=[item.id for item in ready],
                top_k=args.top_k,
            )

        terms = query_terms(args.query)
        excerpts = build_excerpts(
            retrieved,
            terms=terms,
            max_chars=self.settings.rag_context_max_chars,
            per_file_max_excerpts=self.settings.rag_max_excerpts_per_file,
        )
        content = build_rag_block(
            excerpts, attachments=[attachment_snapshot(item) for item in scoped]
        )
        data = self._data(
            wanted,
            [item.id for item in ready],
            skipped,
            [item.id for item in not_ready],
            retrieved,
            excerpts,
        )

        if not excerpts:
            return ToolResult(
                status="empty",
                content=content or NO_MATCH_MESSAGE,
                message="没有找到相关片段",
                data=data,
            )

        return ToolResult(
            content=content or NO_MATCH_MESSAGE,
            message=f"在 {len(ready)} 个文件中找到 {len(excerpts)} 个相关片段",
            sources=tuple(
                build_source_payloads(
                    excerpts,
                    terms=terms,
                    snippet_chars=self.settings.rag_source_snippet_chars,
                )
            ),
            data=data,
        )

    @staticmethod
    def _resolve_scope(
        file_ids: list[int] | None, allowed: tuple[int, ...]
    ) -> tuple[int, ...]:
        if file_ids is None:
            return allowed
        deduped = tuple(dict.fromkeys(file_ids))
        outside = set(deduped) - set(allowed)
        if outside:
            raise ToolError("只能检索本轮消息附带的文件", code="file_scope_denied")
        return deduped

    @staticmethod
    def _in_conversation(attachment: Attachment, context: ToolContext) -> bool:
        if attachment.conversation_id is None:
            return True
        if context.conversation_id is None:
            return False
        return attachment.conversation_id == context.conversation_id

    @staticmethod
    def _not_ready_error(not_ready: list[Attachment]) -> ToolError:
        failed = [
            item
            for item in not_ready
            if item.parse_status in (PARSE_FAILED, PARSE_UNSUPPORTED)
        ]
        if failed:
            return ToolUnavailable("文件解析失败或格式不支持，无法检索")
        return ToolUnavailable("文件尚未解析完成，请稍后再试")

    @staticmethod
    def _data(
        requested: tuple[int, ...],
        searched: list[int],
        skipped: list[int],
        not_ready: list[int],
        retrieved: list,
        excerpts: list,
    ) -> dict[str, object]:
        return {
            "requested_file_ids": list(requested),
            "searched_file_ids": searched,
            "skipped_file_ids": skipped,
            "not_ready_file_ids": not_ready,
            "retrieved_chunks": len(retrieved),
            "excerpt_count": len(excerpts),
        }
