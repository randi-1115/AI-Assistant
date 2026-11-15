"""把检索结果组装成提示词块、来源摘要与 SSE 载荷。

纯函数，不碰数据库；测试可以直接喂 RetrievedChunk。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.ai.prompts.system import RAG_GROUNDING_PROMPT
from app.rag.retriever import RetrievedChunk

_WHITESPACE = re.compile(r"\s+")
_CITATION = re.compile(r"\[资料\s*(\d+)\]")


@dataclass(frozen=True, slots=True)
class RagExcerpt:
    number: int
    chunk: RetrievedChunk
    text: str


def make_snippet(content: str, terms: Sequence[str], max_chars: int) -> str:
    """以首个命中词为中心截取摘要；没有命中就从开头截。"""
    if max_chars <= 0:
        return ""
    compact = _WHITESPACE.sub(" ", content).strip()
    if len(compact) <= max_chars:
        return compact

    lowered = compact.casefold()
    position = -1
    for term in terms:
        found = lowered.find(term.casefold())
        if found >= 0:
            position = found
            break

    if position < 0:
        start = 0
    else:
        start = max(0, position - max_chars // 3)
    end = min(len(compact), start + max_chars)
    snippet = compact[start:end].strip()
    if start > 0:
        snippet = f"…{snippet}"
    if end < len(compact):
        snippet = f"{snippet}…"
    return snippet


def build_excerpts(
    chunks: Sequence[RetrievedChunk],
    *,
    terms: Sequence[str],
    max_chars: int,
    per_file_max_excerpts: int | None = None,
) -> list[RagExcerpt]:
    """按文件 round-robin 累加片段，编号连续，直到预算用完。

    单文件时不做每文件上限，避免小文档的最后几块被无谓丢弃；
    多文件时默认每文件最多 N 块，保证来源公平。
    """
    if max_chars <= 0 or not chunks:
        return []

    groups: dict[int, list[RetrievedChunk]] = {}
    order: list[int] = []
    for chunk in chunks:
        if chunk.attachment_id not in groups:
            groups[chunk.attachment_id] = []
            order.append(chunk.attachment_id)
        groups[chunk.attachment_id].append(chunk)

    if per_file_max_excerpts is None or len(order) == 1:
        per_file_limit = max(1, len(chunks))
    else:
        per_file_limit = max(1, per_file_max_excerpts)

    excerpts: list[RagExcerpt] = []
    remaining = max_chars
    number = 0
    positions = {attachment_id: 0 for attachment_id in order}
    counts = {attachment_id: 0 for attachment_id in order}
    while remaining > 0:
        progressed = False
        for attachment_id in order:
            if remaining <= 0:
                break
            group = groups[attachment_id]
            position = positions[attachment_id]
            if position >= len(group) or counts[attachment_id] >= per_file_limit:
                continue
            progressed = True
            chunk = group[position]
            positions[attachment_id] = position + 1
            text = chunk.content.strip()
            if not text:
                continue
            if len(text) > remaining:
                text = text[:remaining]
            number += 1
            excerpts.append(RagExcerpt(number=number, chunk=chunk, text=text))
            remaining -= len(text)
            counts[attachment_id] += 1
        if not progressed:
            break
    return excerpts


def build_rag_block(
    excerpts: Sequence[RagExcerpt],
    *,
    attachments: Sequence[dict[str, Any]],
) -> str | None:
    if not excerpts:
        if attachments:
            names = "、".join(str(item.get("name", "未知文件")) for item in attachments)
            return (
                f"{RAG_GROUNDING_PROMPT}\n\n"
                f"用户附带了文件（{names}），文件可能已经解析成功，但按当前问题"
                "没有检索到匹配的片段。请明确告知用户“没有找到与问题直接相关的"
                "片段”，不要断言文件为空，也不要编造内容；可以建议用户换一个"
                "更具体的问法或指定章节。"
            )
        return None

    lines = [RAG_GROUNDING_PROMPT, ""]
    for excerpt in excerpts:
        chunk = excerpt.chunk
        heading = " > ".join(chunk.heading_path) if chunk.heading_path else chunk.section_title
        section = f" ｜ 章节：{heading}" if heading else ""
        lines.append(
            f"[资料 {excerpt.number}] 文件：{chunk.file_name} ｜ "
            f"片段：{chunk.chunk_index + 1}{section}"
        )
        lines.append(excerpt.text)
        lines.append("")
    return "\n".join(lines).strip()


def build_source_payloads(
    excerpts: Sequence[RagExcerpt],
    *,
    terms: Sequence[str],
    snippet_chars: int,
) -> list[dict[str, Any]]:
    """只返回实际进入提示词的 excerpts，避免 UI 出现模型没看到的片段。"""
    return [
        {
            "kind": "file",
            "file_id": excerpt.chunk.attachment_id,
            "chunk_index": excerpt.chunk.chunk_index,
            "title": excerpt.chunk.file_name,
            "url": None,
            "section_title": excerpt.chunk.section_title,
            "snippet": make_snippet(excerpt.chunk.content, terms, snippet_chars),
            "score": excerpt.chunk.score,
            "excerpt_number": excerpt.number,
            "citation_label": f"[资料 {excerpt.number}]",
        }
        for excerpt in excerpts
    ]


def validate_citations(
    answer: str,
    sources: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    cited = [int(match) for match in _CITATION.findall(answer)]
    available = {
        int(source["excerpt_number"])
        for source in sources
        if isinstance(source.get("excerpt_number"), int)
    }
    valid = [number for number in cited if number in available]
    invalid = [number for number in cited if number not in available]
    return {
        "cited": cited,
        "valid": valid,
        "invalid": invalid,
        "coverage": (len(valid) / len(cited)) if cited else None,
    }
