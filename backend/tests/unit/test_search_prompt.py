"""联网搜索提示词、来源载荷与上下文合并。"""

from __future__ import annotations

from app.ai.providers.base import ChatMessage
from app.search.base import SearchResult
from app.search.pipeline import (
    build_web_block,
    build_web_empty_block,
    build_web_source_payloads,
    build_web_unavailable_block,
    merge_web_context,
)


def result(
    title: str = "示例标题",
    url: str = "https://example.com/a",
    snippet: str = "网页摘要",
    published_date: str | None = None,
) -> SearchResult:
    return SearchResult(
        title=title, url=url, snippet=snippet, published_date=published_date
    )


def test_build_web_block_contains_boundary_and_source() -> None:
    block = build_web_block(
        [result(published_date="2026-11-12")], max_chars=2000
    )

    assert block is not None
    assert "<web_results>" in block
    assert "</web_results>" in block
    assert "不可信" in block
    assert "标题（URL）" in block
    assert "示例标题" in block
    assert "https://example.com/a" in block
    assert "2026-11-12" in block
    assert "网页摘要" in block


def test_build_web_block_respects_budget() -> None:
    results = [result(snippet="x" * 300) for _ in range(5)]

    block = build_web_block(results, max_chars=1200)

    assert block is not None
    assert len(block) <= 1200


def test_build_web_block_empty_or_tiny_budget() -> None:
    assert build_web_block([], max_chars=1000) is None
    # 预算比提示词本身还小，无法塞入任何结果。
    assert build_web_block([result()], max_chars=10) is None


def test_empty_and_unavailable_blocks_forbid_fabrication() -> None:
    for block in (
        build_web_empty_block("没有结果"),
        build_web_unavailable_block("鉴权失败"),
    ):
        assert "不要编造来源" in block
        assert "明确告知用户" in block


def test_source_payloads() -> None:
    payloads = build_web_source_payloads(
        [result(snippet="x" * 500, published_date="2026-11-12")],
        snippet_chars=20,
    )

    assert payloads[0]["kind"] == "web"
    assert payloads[0]["title"] == "示例标题"
    assert payloads[0]["url"] == "https://example.com/a"
    assert len(payloads[0]["snippet"]) == 20
    assert payloads[0]["published_date"] == "2026-11-12"


def test_merge_web_context_appends_to_existing_system() -> None:
    messages = [
        ChatMessage(role="system", content="基础提示"),
        ChatMessage(role="user", content="你好"),
    ]

    merged = merge_web_context(messages, "<web_results>网页</web_results>")

    assert len(merged) == 2
    assert merged[0].role == "system"
    assert "基础提示" in merged[0].content
    assert "网页" in merged[0].content
    assert merged[1].content == "你好"


def test_merge_web_context_inserts_system_when_missing() -> None:
    merged = merge_web_context([ChatMessage(role="user", content="你好")], "网页资料")

    assert len(merged) == 2
    assert merged[0].role == "system"
    assert merged[0].content == "网页资料"


def test_merge_web_context_none_is_noop() -> None:
    messages = [ChatMessage(role="user", content="你好")]

    merged = merge_web_context(messages, None)

    assert merged == messages
    assert merged is not messages
