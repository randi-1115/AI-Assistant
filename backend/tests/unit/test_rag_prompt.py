"""RAG 提示词与来源载荷组装。"""

from __future__ import annotations

from app.rag.pipeline import (
    build_excerpts,
    build_rag_block,
    build_source_payloads,
    make_snippet,
    validate_citations,
)
from app.rag.retriever import RetrievedChunk


def chunk(
    attachment_id: int = 1,
    file_name: str = "报告.pdf",
    chunk_index: int = 0,
    section_title: str | None = "第 1 页",
    content: str = "文件上传与检索的核心结论。",
    score: float = 0.9,
    heading_path: tuple[str, ...] = (),
) -> RetrievedChunk:
    return RetrievedChunk(
        attachment_id=attachment_id,
        file_name=file_name,
        chunk_index=chunk_index,
        section_title=section_title,
        content=content,
        heading_path=heading_path,
        score=score,
    )


def test_make_snippet_centers_on_term() -> None:
    content = "前" * 100 + "关键字" + "后" * 100
    snippet = make_snippet(content, ["关键字"], 20)
    assert "关键字" in snippet
    assert snippet.startswith("…")
    assert snippet.endswith("…")


def test_build_excerpts_respects_budget() -> None:
    chunks = [chunk(content="a" * 60), chunk(content="b" * 60)]
    excerpts = build_excerpts(chunks, terms=[], max_chars=80)
    assert len(excerpts) == 2
    assert sum(len(excerpt.text) for excerpt in excerpts) <= 80


def test_build_rag_block_contains_source_clues() -> None:
    excerpts = build_excerpts([chunk()], terms=[], max_chars=1000)
    block = build_rag_block(excerpts, attachments=[{"name": "报告.pdf"}])
    assert block is not None
    assert "报告.pdf" in block
    assert "片段：1" in block
    assert "第 1 页" in block
    assert "资料不足" in block


def test_build_rag_block_notes_missing_text() -> None:
    block = build_rag_block([], attachments=[{"name": "空.pdf"}])
    assert block is not None
    assert "空.pdf" in block
    assert "资料不足" in block


def test_build_rag_block_none_without_attachments() -> None:
    assert build_rag_block([], attachments=[]) is None


def test_excerpt_numbers_are_contiguous_and_skip_empty() -> None:
    excerpts = build_excerpts(
        [chunk(chunk_index=0, content=""), chunk(chunk_index=1, content="有效内容")],
        terms=[],
        max_chars=1000,
    )
    assert [excerpt.number for excerpt in excerpts] == [1]
    assert excerpts[0].chunk.chunk_index == 1


def test_excerpts_are_round_robin_across_files() -> None:
    excerpts = build_excerpts(
        [
            chunk(attachment_id=1, chunk_index=0, content="A0"),
            chunk(attachment_id=1, chunk_index=1, content="A1"),
            chunk(attachment_id=2, chunk_index=0, content="B0"),
            chunk(attachment_id=2, chunk_index=1, content="B1"),
        ],
        terms=[],
        max_chars=1000,
        per_file_max_excerpts=1,
    )
    assert [(item.chunk.attachment_id, item.chunk.chunk_index) for item in excerpts] == [
        (1, 0),
        (2, 0),
    ]


def test_single_file_ignores_per_file_cap() -> None:
    excerpts = build_excerpts(
        [
            chunk(attachment_id=1, chunk_index=0, content="A0"),
            chunk(attachment_id=1, chunk_index=1, content="A1"),
        ],
        terms=[],
        max_chars=1000,
        per_file_max_excerpts=1,
    )
    assert len(excerpts) == 2


def test_sources_only_include_actual_excerpts() -> None:
    excerpts = build_excerpts(
        [chunk(content="甲" * 10), chunk(chunk_index=1, content="乙" * 10)],
        terms=[],
        max_chars=10,
    )
    payloads = build_source_payloads(excerpts, terms=[], snippet_chars=50)
    assert len(payloads) == 1
    assert payloads[0]["excerpt_number"] == 1
    assert payloads[0]["citation_label"] == "[资料 1]"


def test_validate_citations() -> None:
    report = validate_citations(
        "结论见[资料 1]，另一个说法见[资料 3]。",
        [{"excerpt_number": 1}, {"excerpt_number": 2}],
    )
    assert report["cited"] == [1, 3]
    assert report["valid"] == [1]
    assert report["invalid"] == [3]
    assert report["coverage"] == 0.5


def test_rag_block_uses_heading_path() -> None:
    excerpts = build_excerpts(
        [chunk(heading_path=("第一章", "反向传播"))],
        terms=[],
        max_chars=1000,
    )
    block = build_rag_block(excerpts, attachments=[{"name": "报告.pdf"}])
    assert block is not None
    assert "章节：第一章 > 反向传播" in block
    assert "[资料 1]" in block


def test_source_payload_shape() -> None:
    excerpts = build_excerpts(
        [chunk(chunk_index=4, score=0.87)],
        terms=["文件"],
        max_chars=1000,
    )
    payloads = build_source_payloads(
        excerpts,
        terms=["文件"],
        snippet_chars=50,
    )
    payload = payloads[0]
    assert payload["kind"] == "file"
    assert payload["file_id"] == 1
    assert payload["chunk_index"] == 4
    assert payload["title"] == "报告.pdf"
    assert payload["url"] is None
    assert payload["section_title"] == "第 1 页"
    assert payload["score"] == 0.87
    assert payload["snippet"]
    assert payload["excerpt_number"] == 1
    assert payload["citation_label"] == "[资料 1]"
