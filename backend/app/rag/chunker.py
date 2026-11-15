"""文档切块。

按 section（页/标题）分别处理，避免不同页的内容被硬拼进同一块；块与块之间
保留一小段 overlap，让跨段落的问题也能命中。overlap 计入预算，单块不会超过
chunk_size；表格按整行打包并重复表头。切块是纯函数，便于单测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.rag.parser_base import ParsedDocument

_MULTI_BLANK = re.compile(r"\n{3,}")
_SENTENCE_END = re.compile(r"[。！？!?；;.]\s*|\n")


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    section_title: str | None
    content: str
    char_count: int
    metadata: dict[str, Any]


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()


def _split_long_paragraph(paragraph: str, chunk_size: int) -> list[str]:
    """把超长段落切成 <= chunk_size 的片段，优先在句末/换行处断开。"""
    pieces: list[str] = []
    remaining = paragraph
    while len(remaining) > chunk_size:
        window = remaining[:chunk_size]
        cut = -1
        for match in _SENTENCE_END.finditer(window):
            if match.end() >= chunk_size // 2:
                cut = match.end()
        if cut <= 0:
            cut = chunk_size
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    if remaining.strip():
        pieces.append(remaining.strip())
    return [piece for piece in pieces if piece]


def _cut_at_budget(text: str, budget: int) -> tuple[str, str]:
    if budget <= 0:
        return "", text
    if len(text) <= budget:
        return text, ""
    window = text[:budget]
    cut = -1
    for match in _SENTENCE_END.finditer(window):
        if match.end() >= budget // 2:
            cut = match.end()
    if cut <= 0:
        cut = budget
    return text[:cut].strip(), text[cut:].lstrip()


def _paragraphs(text: str, chunk_size: int) -> list[str]:
    paragraphs: list[str] = []
    for raw in re.split(r"\n\s*\n", text):
        paragraph = raw.strip()
        if not paragraph:
            continue
        if len(paragraph) <= chunk_size:
            paragraphs.append(paragraph)
        else:
            paragraphs.extend(_split_long_paragraph(paragraph, chunk_size))
    return paragraphs


def _table_paragraphs(text: str, chunk_size: int) -> list[str]:
    """表格按整行打包，并在每个块重复表头。"""
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    if not rows:
        return []
    header = rows[0]
    body = rows[1:] if len(rows) > 1 else []
    if not body:
        return [header]

    pieces: list[str] = []
    current = header
    for row in body:
        candidate = f"{current}\n{row}"
        if len(candidate) > chunk_size and current != header:
            pieces.append(current)
            current = f"{header}\n{row}"
        else:
            current = candidate
    if current.strip():
        pieces.append(current)
    return pieces


def chunk_document(
    document: ParsedDocument,
    *,
    chunk_size: int,
    overlap: int,
    max_chunks: int,
) -> list[Chunk]:
    if chunk_size <= 0 or max_chunks <= 0:
        return []

    chunks: list[Chunk] = []
    index = 0

    for section_index, section in enumerate(document.sections):
        text = _normalize(section.text)
        if not text:
            continue

        paragraphs = (
            _table_paragraphs(text, chunk_size)
            if section.kind == "table"
            else _paragraphs(text, chunk_size)
        )
        if not paragraphs:
            continue

        previous_tail = ""
        position = 0
        while position < len(paragraphs):
            budget = max(
                1,
                chunk_size - (len(previous_tail) + 1 if previous_tail else 0),
            )
            buffer: list[str] = []
            length = 0

            while position < len(paragraphs):
                paragraph = paragraphs[position]
                addition = len(paragraph) + (2 if buffer else 0)
                if buffer and length + addition > budget:
                    break
                if not buffer and len(paragraph) > budget:
                    head, rest = _cut_at_budget(paragraph, budget)
                    if not head:
                        head, rest = paragraph[:budget], paragraph[budget:]
                    buffer = [head]
                    length = len(head)
                    if rest:
                        paragraphs[position] = rest
                    else:
                        position += 1
                    break
                buffer.append(paragraph)
                length += addition
                position += 1

            if not buffer:
                break

            body = "\n\n".join(buffer).strip()
            if not body:
                continue
            content = f"{previous_tail}\n{body}" if previous_tail and overlap > 0 else body
            if len(content) > chunk_size:
                content = content[:chunk_size].rstrip()
            if not content:
                continue
            chunks.append(
                Chunk(
                    index=index,
                    section_title=section.title,
                    content=content,
                    char_count=len(content),
                    metadata={
                        "section_index": section_index,
                        "heading_path": list(section.heading_path),
                        "kind": section.kind,
                        "truncated": section.truncated,
                    },
                )
            )
            previous_tail = content[-overlap:] if overlap > 0 else ""
            index += 1
            if index >= max_chunks:
                return chunks

    return chunks
