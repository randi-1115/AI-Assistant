"""关键词切分与打分。"""

from __future__ import annotations

from app.rag.retriever import (
    CandidateChunk,
    compute_idf,
    dedupe_by_content,
    is_summary_query,
    query_terms,
    rank_chunks,
    score_chunk,
    searchable_text,
    select_baseline_chunks,
    tokenize,
)


def candidate(
    attachment_id: int = 1,
    file_name: str = "a.txt",
    chunk_index: int = 0,
    section_title: str | None = None,
    content: str = "",
    heading_path: tuple[str, ...] = (),
    kind: str = "text",
) -> CandidateChunk:
    return CandidateChunk(
        attachment_id,
        file_name,
        chunk_index,
        section_title,
        content,
        heading_path,
        kind,
    )


def test_chinese_bigrams() -> None:
    assert tokenize("上传文件") == ["上传", "传文", "文件"]


def test_mixed_chinese_and_english() -> None:
    assert tokenize("Python 装饰器") == ["python", "装饰", "饰器"]


def test_full_width_normalized() -> None:
    assert tokenize("ＰＤＦ解析") == ["pdf", "解析"]


def test_stopwords_only() -> None:
    assert query_terms("the and is") == []
    assert query_terms("的 了 是") == []


def test_single_cjk_char_kept() -> None:
    assert query_terms("猫") == ["猫"]


def test_common_bigrams_are_filtered() -> None:
    assert tokenize("的是 我们 这个") == []


def test_idf_boosts_rare_terms() -> None:
    candidates = [
        candidate(1, "a.txt", 0, None, "模型 量子"),
        candidate(2, "b.txt", 0, None, "模型 经典"),
    ]
    idf = compute_idf(candidates, ["模型", "量子"])
    assert idf["量子"] > idf["模型"]


def test_heading_path_is_searchable() -> None:
    chunk = candidate(
        content="正文没有关键词",
        section_title="反向传播",
        heading_path=("第一章", "反向传播"),
    )
    assert "反向传播" in searchable_text(chunk)
    ranked = rank_chunks("反向传播", [chunk], top_k=4)
    assert ranked and ranked[0].score > 0


def test_dedupe_by_content() -> None:
    chunks = [
        candidate(1, "a.txt", 0, None, "相同内容"),
        candidate(2, "b.txt", 0, None, "相同内容"),
    ]
    assert len(dedupe_by_content(chunks)) == 1


def test_query_terms_dedupe_preserving_order() -> None:
    assert query_terms("文件上传 文件") == ["文件", "件上", "上传"]


def test_exact_phrase_outranks_scattered_terms() -> None:
    exact = candidate(content="这里讲文件上传的完整流程。")
    scattered = candidate(content="文件系统里有一个上传按钮，但讲的是别的东西。")
    terms = query_terms("文件上传")
    assert score_chunk(terms, "文件上传", exact) > score_chunk(terms, "文件上传", scattered)


def test_title_only_match_does_not_surface() -> None:
    chunk = candidate(file_name="文件上传.txt", content="完全无关的内容")
    assert score_chunk(query_terms("文件上传"), "文件上传", chunk) == 0.0


def test_title_boost_breaks_ties() -> None:
    plain = candidate(content="同一段内容")
    titled = candidate(file_name="内容说明.txt", content="同一段内容")
    terms = query_terms("内容")
    assert score_chunk(terms, "内容", titled) > score_chunk(terms, "内容", plain)


def test_tf_saturates() -> None:
    thrice = candidate(content="内容" * 3)
    many = candidate(content="内容" * 100)
    terms = query_terms("内容")
    assert score_chunk(terms, "内容", many) == score_chunk(terms, "内容", thrice)


def test_rank_no_match_returns_empty() -> None:
    chunks = [candidate(content="完全无关")]
    assert rank_chunks("文件上传", chunks, top_k=4) == []


def test_rank_is_deterministic_for_ties() -> None:
    chunks = [
        candidate(attachment_id=2, file_name="b.txt", chunk_index=0, content="相同"),
        candidate(attachment_id=1, file_name="a.txt", chunk_index=0, content="相同"),
    ]
    first = rank_chunks("相同", chunks, top_k=4)
    second = rank_chunks("相同", list(reversed(chunks)), top_k=4)
    assert [chunk.file_name for chunk in first] == ["a.txt", "b.txt"]
    assert [chunk.file_name for chunk in second] == ["a.txt", "b.txt"]


def test_summary_query_detection() -> None:
    assert is_summary_query("总结这个文件")
    assert is_summary_query("Summarize this document")
    assert not is_summary_query("反向传播是什么")


def test_baseline_includes_small_file_entirely() -> None:
    chunks = [
        candidate(attachment_id=1, file_name="a.docx", chunk_index=index, content=f"内容{index}")
        for index in range(3)
    ]

    baseline = select_baseline_chunks(chunks, top_k=4)

    assert [chunk.chunk_index for chunk in baseline] == [0, 1, 2]
    assert all(chunk.score == 0.0 for chunk in baseline)


def test_baseline_spreads_across_multiple_files() -> None:
    chunks = [
        candidate(attachment_id=1, file_name="a.docx", chunk_index=index, content=f"a{index}")
        for index in range(10)
    ] + [
        candidate(attachment_id=2, file_name="b.docx", chunk_index=index, content=f"b{index}")
        for index in range(10)
    ]

    baseline = select_baseline_chunks(chunks, top_k=4)

    assert len(baseline) == 4
    assert {chunk.attachment_id for chunk in baseline} == {1, 2}
    assert sorted(chunk.chunk_index for chunk in baseline if chunk.attachment_id == 1) == [0, 9]
    assert sorted(chunk.chunk_index for chunk in baseline if chunk.attachment_id == 2) == [0, 9]


def test_per_file_cap_then_fill() -> None:
    chunks = [
        candidate(attachment_id=1, file_name="a.txt", chunk_index=0, content="内容一"),
        candidate(attachment_id=1, file_name="a.txt", chunk_index=1, content="内容二"),
        candidate(attachment_id=2, file_name="b.txt", chunk_index=0, content="内容三"),
        candidate(attachment_id=2, file_name="b.txt", chunk_index=1, content="内容四"),
    ]
    ranked = rank_chunks("内容", chunks, top_k=3, per_file_k=1)
    assert len(ranked) == 3
    assert {chunk.attachment_id for chunk in ranked} == {1, 2}
