"""文档检索工具：作用域、归属、就绪状态与结果组装（不碰真实数据库）。"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.models.attachment import Attachment
from app.rag.retriever import RetrievedChunk
from app.tools import document_search as module
from app.tools.base import ToolContext, ToolError, ToolUnavailable
from app.tools.document_search import DocumentSearchArgs, DocumentSearchTool
from app.tools.registry import ToolRegistry


def make_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        database_url="mysql+aiomysql://user:pass@localhost/db",
        **overrides,
    )


def attachment(
    file_id: int,
    *,
    conversation_id: int | None = None,
    name: str = "report.txt",
    status: str = "ready",
) -> Attachment:
    return Attachment(
        id=file_id,
        user_id=1,
        conversation_id=conversation_id,
        original_name=name,
        storage_key=f"key-{file_id}",
        extension="txt",
        size_bytes=10,
        parse_status=status,
    )


def chunk(file_id: int = 1, content: str = "项目预算是一百万元。") -> RetrievedChunk:
    return RetrievedChunk(
        attachment_id=file_id,
        file_name="report.txt",
        chunk_index=0,
        section_title=None,
        content=content,
        score=0.9,
    )


class FakeSession:
    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakeSessionFactory:
    def __call__(self) -> FakeSession:
        return FakeSession()


class FakeRepo:
    def __init__(self, attachments: list[Attachment]) -> None:
        self.attachments = attachments
        self.calls: list[tuple[int, tuple[int, ...]]] = []

    async def list_owned_by_ids(self, user_id: int, file_ids) -> list[Attachment]:
        self.calls.append((user_id, tuple(file_ids)))
        wanted = set(file_ids)
        return [item for item in self.attachments if item.id in wanted]


class FakeRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.last: dict[str, object] = {}

    async def retrieve(self, *, user_id, query, attachment_ids, top_k=None):
        self.last = {
            "user_id": user_id,
            "query": query,
            "attachment_ids": list(attachment_ids),
            "top_k": top_k,
        }
        return self.chunks


def install(monkeypatch, attachments: list[Attachment], chunks: list[RetrievedChunk]):
    repo = FakeRepo(attachments)
    retriever = FakeRetriever(chunks)
    monkeypatch.setattr(module, "AttachmentRepository", lambda session: repo)
    monkeypatch.setattr(module, "KeywordRetriever", lambda session, settings: retriever)
    return repo, retriever


def context(attachment_ids=(1,), conversation_id=1, session_factory=None) -> ToolContext:
    return ToolContext(
        user_id=1,
        conversation_id=conversation_id,
        attachment_ids=tuple(attachment_ids),
        session_factory=session_factory or FakeSessionFactory(),
    )


async def run_tool(args: DocumentSearchArgs, ctx: ToolContext):
    return await DocumentSearchTool(make_settings()).run(args, ctx)


async def test_success_returns_content_and_file_sources(monkeypatch) -> None:
    install(monkeypatch, [attachment(1)], [chunk(1)])
    result = await run_tool(DocumentSearchArgs(query="预算"), context())

    assert result.status == "success"
    assert "[资料 1]" in result.content
    assert "report.txt" in result.content
    assert result.sources[0]["kind"] == "file"
    assert result.sources[0]["file_id"] == 1
    assert result.data["searched_file_ids"] == [1]
    assert result.data["excerpt_count"] == 1


async def test_default_scope_uses_context_attachments(monkeypatch) -> None:
    repo, retriever = install(
        monkeypatch, [attachment(1), attachment(2)], [chunk(1)]
    )
    await run_tool(DocumentSearchArgs(query="预算"), context((1, 2)))
    assert repo.calls == [(1, (1, 2))]
    assert retriever.last["attachment_ids"] == [1, 2]


async def test_explicit_file_ids_can_only_narrow_scope(monkeypatch) -> None:
    _, retriever = install(monkeypatch, [attachment(1), attachment(2)], [chunk(2)])
    await run_tool(DocumentSearchArgs(query="预算", file_ids=[2]), context((1, 2)))
    assert retriever.last["attachment_ids"] == [2]


async def test_explicit_file_ids_outside_scope_are_denied_without_query(monkeypatch) -> None:
    repo, _ = install(monkeypatch, [attachment(3)], [chunk(3)])
    with pytest.raises(ToolError) as excinfo:
        await run_tool(DocumentSearchArgs(query="预算", file_ids=[3]), context((1, 2)))
    assert excinfo.value.code == "file_scope_denied"
    assert repo.calls == []


async def test_empty_explicit_scope_returns_empty_without_session() -> None:
    result = await DocumentSearchTool(make_settings()).run(
        DocumentSearchArgs(query="预算", file_ids=[]),
        context((1,), session_factory=None),
    )
    assert result.status == "empty"


async def test_no_attachments_returns_empty() -> None:
    result = await DocumentSearchTool(make_settings()).run(
        DocumentSearchArgs(query="预算"), context(())
    )
    assert result.status == "empty"
    assert "没有附带文件" in result.content


async def test_duplicate_context_ids_are_deduped(monkeypatch) -> None:
    _, retriever = install(monkeypatch, [attachment(1), attachment(2)], [chunk(1)])
    await run_tool(DocumentSearchArgs(query="预算"), context((1, 1, 2, 2)))
    assert retriever.last["attachment_ids"] == [1, 2]


async def test_missing_default_attachment_is_skipped(monkeypatch) -> None:
    install(monkeypatch, [attachment(1)], [chunk(1)])
    result = await run_tool(DocumentSearchArgs(query="预算"), context((1, 2)))
    assert result.status == "success"
    assert result.data["skipped_file_ids"] == [2]


async def test_cross_conversation_attachment_is_skipped_by_default(monkeypatch) -> None:
    install(monkeypatch, [attachment(1, conversation_id=99)], [chunk(1)])
    result = await run_tool(DocumentSearchArgs(query="预算"), context((1,), conversation_id=1))
    assert result.status == "empty"


async def test_cross_conversation_attachment_is_denied_when_explicit(monkeypatch) -> None:
    install(monkeypatch, [attachment(1, conversation_id=99)], [chunk(1)])
    with pytest.raises(ToolError) as excinfo:
        await run_tool(
            DocumentSearchArgs(query="预算", file_ids=[1]),
            context((1,), conversation_id=1),
        )
    assert excinfo.value.code == "file_scope_denied"


async def test_unready_attachment_is_unavailable(monkeypatch) -> None:
    install(monkeypatch, [attachment(1, status="pending")], [])
    with pytest.raises(ToolUnavailable) as excinfo:
        await run_tool(DocumentSearchArgs(query="预算"), context())
    assert "尚未解析完成" in str(excinfo.value)


async def test_failed_attachment_reports_parse_failure(monkeypatch) -> None:
    install(monkeypatch, [attachment(1, status="failed")], [])
    with pytest.raises(ToolUnavailable) as excinfo:
        await run_tool(DocumentSearchArgs(query="预算"), context())
    assert "解析失败" in str(excinfo.value)


async def test_mixed_ready_and_unready_searches_ready_subset(monkeypatch) -> None:
    _, retriever = install(
        monkeypatch,
        [attachment(1), attachment(2, status="pending")],
        [chunk(1)],
    )
    result = await run_tool(DocumentSearchArgs(query="预算"), context((1, 2)))
    assert result.status == "success"
    assert retriever.last["attachment_ids"] == [1]
    assert result.data["not_ready_file_ids"] == [2]


async def test_no_matching_chunks_returns_empty_with_grounding_text(monkeypatch) -> None:
    install(monkeypatch, [attachment(1)], [])
    result = await run_tool(DocumentSearchArgs(query="预算"), context())
    assert result.status == "empty"
    assert "没有找到" in result.content
    assert result.sources == ()


async def test_top_k_is_forwarded(monkeypatch) -> None:
    _, retriever = install(monkeypatch, [attachment(1)], [chunk(1)])
    await run_tool(DocumentSearchArgs(query="预算", top_k=2), context())
    assert retriever.last["top_k"] == 2


async def test_none_session_factory_fails_cleanly() -> None:
    ctx = ToolContext(user_id=1, attachment_ids=(1,), session_factory=None)
    with pytest.raises(ToolError) as excinfo:
        await DocumentSearchTool(make_settings()).run(
            DocumentSearchArgs(query="预算"), ctx
        )
    assert excinfo.value.code == "internal_error"


def test_blank_query_is_rejected_by_registry() -> None:
    registry = ToolRegistry()
    registry.register(DocumentSearchTool(make_settings()))
    execution = asyncio.run(
        registry.execute("document_search", {"query": "   "}, context())
    )
    assert execution.error_code == "invalid_arguments"
