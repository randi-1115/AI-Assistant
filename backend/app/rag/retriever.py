"""关键词检索。

首版不接 embedding：对查询做「英文/代码词 + 中文二元组」切分，再在候选块里
按词覆盖率、词频、完整短语和文件名/章节命中做加权评分。

这是纯函数 + 一个薄薄的数据库适配层：
- tokenize / score_chunk / rank_chunks 不碰数据库，可以单独测试；
- KeywordRetriever 只负责从 repository 取候选块。
将来换向量检索时，实现同一个 Retriever  Protocol 即可。
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.repositories.attachment_repo import AttachmentRepository

ASCII_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "have", "he", "her", "his", "i", "in", "is", "it", "its",
        "of", "on", "or", "she", "that", "the", "their", "them", "they",
        "this", "to", "was", "were", "will", "with", "you", "your",
    }
)
CJK_STOPWORDS: frozenset[str] = frozenset(
    {"的", "了", "是", "在", "和", "我", "你", "他", "她", "它", "吗", "呢", "吧", "这", "那"}
)

_TOKEN = re.compile(
    r"[a-z0-9_]+"
    r"|[㐀-䶿一-鿿豈-﫿]+"
)
_WHITESPACE = re.compile(r"\s+")
MAX_QUERY_TERMS = 64

# 总结/概括类指令通常没有任何正文关键词，必须回退到文档的代表性片段。
SUMMARY_TERMS = (
    "总结",
    "概括",
    "摘要",
    "归纳",
    "概述",
    "主要内容",
    "讲了什么",
    "说了什么",
    "summarize",
    "summary",
    "overview",
    "tldr",
)


def is_summary_query(query: str) -> bool:
    lowered = query.casefold()
    return any(term in lowered for term in SUMMARY_TERMS)


@dataclass(frozen=True, slots=True)
class CandidateChunk:
    attachment_id: int
    file_name: str
    chunk_index: int
    section_title: str | None
    content: str
    heading_path: tuple[str, ...] = ()
    kind: str = "text"


@dataclass(frozen=True, slots=True)
class RetrievedChunk(CandidateChunk):
    score: float = 0.0


COMMON_CJK_BIGRAMS: frozenset[str] = frozenset(
    {
        "的是",
        "我们",
        "他们",
        "这个",
        "那个",
        "一个",
        "可以",
        "没有",
        "因为",
        "所以",
        "但是",
        "如果",
        "就是",
        "不是",
        "什么",
        "怎么",
        "以及",
        "并且",
        "或者",
        "进行",
        "通过",
        "使用",
        "需要",
        "可能",
        "已经",
        "还是",
        "然后",
        "其中",
        "对于",
        "关于",
        "这些",
        "那些",
    }
)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _compact(text: str) -> str:
    return _WHITESPACE.sub("", _normalize(text))


def tokenize(text: str) -> list[str]:
    """英文/代码词保持完整，中文连续段拆成二元组（单字段保留单字）。"""
    normalized = _normalize(text)
    terms: list[str] = []
    for match in _TOKEN.finditer(normalized):
        token = match.group(0)
        if token.isascii():
            if len(token) >= 2 and token not in ASCII_STOPWORDS:
                terms.append(token)
            continue
        if len(token) == 1:
            if token not in CJK_STOPWORDS:
                terms.append(token)
            continue
        for index in range(len(token) - 1):
            term = token[index : index + 2]
            if term in COMMON_CJK_BIGRAMS:
                continue
            terms.append(term)
    return terms


def query_terms(text: str) -> list[str]:
    """去重并限制数量，保持原始顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for term in tokenize(text):
        if term in seen:
            continue
        seen.add(term)
        result.append(term)
        if len(result) >= MAX_QUERY_TERMS:
            break
    return result


def searchable_text(chunk: CandidateChunk) -> str:
    """检索文本 = 标题路径 + 章节标题 + 正文；标题-only 也能被召回。"""
    parts = [*chunk.heading_path]
    if chunk.section_title:
        parts.append(chunk.section_title)
    parts.append(chunk.content)
    return " ".join(part for part in parts if part)


def compute_idf(
    candidates: Sequence[CandidateChunk],
    terms: Sequence[str],
) -> dict[str, float]:
    total = max(1, len(candidates))
    df: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        text = _normalize(searchable_text(candidate))
        for term in terms:
            if term in text:
                df[term] += 1
    return {
        term: math.log((total + 1) / (df[term] + 1)) + 1.0
        for term in terms
    }


def dedupe_by_content(candidates: Sequence[CandidateChunk]) -> list[CandidateChunk]:
    """去掉重复上传/完全相同的 chunk；保留第一次出现的顺序。"""
    seen: set[str] = set()
    result: list[CandidateChunk] = []
    for candidate in candidates:
        key = hashlib.sha256(_normalize(candidate.content).encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def score_chunk(
    terms: Sequence[str],
    query_compact: str,
    chunk: CandidateChunk,
    *,
    idf: Mapping[str, float] | None = None,
) -> float:
    if not terms:
        return 0.0

    weights = [(idf or {}).get(term, 1.0) for term in terms]
    total_weight = sum(weights) or 1.0
    normalized = _normalize(searchable_text(chunk))

    matched_weight = 0.0
    tf_weight = 0.0
    for term, weight in zip(terms, weights):
        count = normalized.count(term)
        if count:
            matched_weight += weight
            tf_weight += weight * (min(count, 3) / 3)

    if matched_weight == 0:
        # 只有文件名/章节命的块不能单独冒出来，否则检索会变得不可预测。
        return 0.0

    coverage = matched_weight / total_weight
    tf_score = tf_weight / total_weight
    phrase = (
        1.0
        if len(query_compact) >= 2 and query_compact in _compact(chunk.content)
        else 0.0
    )
    title_text = _normalize(
        " ".join(
            [
                chunk.file_name,
                *chunk.heading_path,
                chunk.section_title or "",
            ]
        )
    )
    title_weight = sum(
        weight for term, weight in zip(terms, weights) if term in title_text
    )
    title_score = title_weight / total_weight

    score = 0.55 * coverage + 0.25 * tf_score + 0.15 * phrase + 0.05 * title_score
    return round(min(max(score, 0.0), 1.0), 6)


def rank_chunks(
    query: str,
    candidates: Sequence[CandidateChunk],
    *,
    top_k: int,
    per_file_k: int | None = None,
) -> list[RetrievedChunk]:
    terms = query_terms(query)
    if not terms or not candidates or top_k <= 0:
        return []

    query_compact = _compact(query)
    idf = compute_idf(candidates, terms)
    scored = [
        RetrievedChunk(
            attachment_id=chunk.attachment_id,
            file_name=chunk.file_name,
            chunk_index=chunk.chunk_index,
            section_title=chunk.section_title,
            content=chunk.content,
            heading_path=chunk.heading_path,
            kind=chunk.kind,
            score=score_chunk(terms, query_compact, chunk, idf=idf),
        )
        for chunk in candidates
    ]
    scored = [chunk for chunk in scored if chunk.score > 0]
    scored.sort(
        key=lambda chunk: (
            -chunk.score,
            chunk.file_name.casefold(),
            chunk.chunk_index,
            chunk.attachment_id,
        )
    )

    if per_file_k is None:
        per_file_k = max(1, top_k // 2)

    selected: list[RetrievedChunk] = []
    leftovers: list[RetrievedChunk] = []
    counts: dict[int, int] = defaultdict(int)
    for chunk in scored:
        if len(selected) >= top_k:
            break
        if counts[chunk.attachment_id] < per_file_k:
            selected.append(chunk)
            counts[chunk.attachment_id] += 1
        else:
            leftovers.append(chunk)

    if len(selected) < top_k:
        for chunk in leftovers:
            if len(selected) >= top_k:
                break
            selected.append(chunk)

    return selected


def select_baseline_chunks(
    candidates: Sequence[CandidateChunk],
    *,
    top_k: int,
) -> list[RetrievedChunk]:
    """没有关键词命中时，用每个文件的代表性块兜底。

    单个文件按首/中/尾均匀取样；多个文件先每个取一块，保证覆盖。
    score=0 表示“不是关键词命中，而是文档基线内容”。
    """
    if not candidates or top_k <= 0:
        return []

    grouped: dict[int, list[CandidateChunk]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.attachment_id, []).append(candidate)

    file_count = len(grouped)
    per_file = max(1, top_k // file_count)
    if file_count == 1:
        per_file = top_k

    selected: list[RetrievedChunk] = []
    for group in grouped.values():
        if len(group) <= per_file:
            picks = group
        elif per_file <= 1:
            picks = [group[0]]
        else:
            indices = sorted(
                {
                    round(index * (len(group) - 1) / (per_file - 1))
                    for index in range(per_file)
                }
            )
            picks = [group[index] for index in indices]
        for candidate in picks:
            selected.append(
                RetrievedChunk(
                    attachment_id=candidate.attachment_id,
                    file_name=candidate.file_name,
                    chunk_index=candidate.chunk_index,
                    section_title=candidate.section_title,
                    content=candidate.content,
                    heading_path=candidate.heading_path,
                    kind=candidate.kind,
                    score=0.0,
                )
            )
            if len(selected) >= top_k:
                return selected
    return selected


class Retriever(Protocol):
    async def retrieve(
        self,
        *,
        user_id: int,
        query: str,
        attachment_ids: Sequence[int],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        ...


class KeywordRetriever:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def retrieve(
        self,
        *,
        user_id: int,
        query: str,
        attachment_ids: Sequence[int],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        if not attachment_ids:
            return []
        repo = AttachmentRepository(self.session)
        # 按附件分别取候选再 round-robin，避免“最新文件/前 2000 行”挤掉旧文件。
        groups = [
            await repo.list_candidates(
                user_id,
                [attachment_id],
                limit=self.settings.rag_candidates_per_file,
            )
            for attachment_id in attachment_ids
        ]
        rows = []
        position = 0
        while (
            len(rows) < self.settings.rag_max_candidate_chunks
            and any(position < len(group) for group in groups)
        ):
            progressed = False
            for group in groups:
                if position < len(group):
                    rows.append(group[position])
                    progressed = True
                    if len(rows) >= self.settings.rag_max_candidate_chunks:
                        break
            if not progressed:
                break
            position += 1

        candidates: list[CandidateChunk] = []
        for chunk, attachment in rows:
            metadata = chunk.metadata_json or {}
            raw_path = metadata.get("heading_path") if isinstance(metadata, dict) else None
            heading_path = (
                tuple(item for item in raw_path if isinstance(item, str))
                if isinstance(raw_path, (list, tuple))
                else ()
            )
            raw_kind = metadata.get("kind") if isinstance(metadata, dict) else None
            candidates.append(
                CandidateChunk(
                    attachment_id=attachment.id,
                    file_name=attachment.original_name,
                    chunk_index=chunk.chunk_index,
                    section_title=chunk.section_title,
                    content=chunk.content,
                    heading_path=heading_path,
                    kind=raw_kind if isinstance(raw_kind, str) else "text",
                )
            )
        candidates = dedupe_by_content(candidates)
        # 工具调用可以显式指定 top_k；默认仍沿用 RAG_TOP_K，老调用方行为不变。
        limit = max(1, self.settings.rag_top_k if top_k is None else top_k)
        ranked = rank_chunks(
            query,
            candidates,
            top_k=limit,
            per_file_k=max(1, limit // 2),
        )

        # 总结类问题通常没有正文关键词，必须回退到文档的代表性片段。
        # 具体问题没命中时保持原有行为，避免把无关片段硬塞进上下文。
        if is_summary_query(query):
            baseline = select_baseline_chunks(candidates, top_k=limit)
            seen = {(chunk.attachment_id, chunk.chunk_index) for chunk in ranked}
            merged = list(ranked)
            for chunk in baseline:
                key = (chunk.attachment_id, chunk.chunk_index)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(chunk)
                if len(merged) >= limit:
                    break
            ranked = merged[:limit]
        return ranked
