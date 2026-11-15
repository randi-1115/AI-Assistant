"""切块：段落打包、超长段、overlap、上限。"""

from __future__ import annotations

from app.rag.chunker import chunk_document
from app.rag.parser_base import ParsedDocument, ParsedSection


def document(*sections: tuple[str | None, str]) -> ParsedDocument:
    return ParsedDocument(
        tuple(ParsedSection(title, text) for title, text in sections),
        "test",
    )


def test_empty_document_returns_no_chunks() -> None:
    assert chunk_document(document(), chunk_size=100, overlap=10, max_chunks=10) == []


def test_short_section_becomes_one_chunk() -> None:
    chunks = chunk_document(
        document(("第一节", "短文本")), chunk_size=100, overlap=10, max_chunks=10
    )
    assert len(chunks) == 1
    assert chunks[0].section_title == "第一节"
    assert chunks[0].content == "短文本"
    assert chunks[0].char_count == len("短文本")


def test_paragraph_packing_and_overlap() -> None:
    sections = document(("标题", "第一段内容甲乙丙\n\n第二段内容丁戊己"))
    chunks = chunk_document(sections, chunk_size=12, overlap=2, max_chunks=10)
    assert len(chunks) == 2
    assert chunks[0].content == "第一段内容甲乙丙"
    # 第二块带上一块的尾部 overlap。
    assert chunks[1].content.startswith("乙丙")
    assert chunks[1].content.endswith("第二段内容丁戊己")


def test_long_paragraph_is_split() -> None:
    text = "这是一句话。" * 20
    chunks = chunk_document(document((None, text)), chunk_size=20, overlap=0, max_chunks=100)
    assert len(chunks) > 1
    assert all(chunk.char_count <= 21 for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks).replace("\n", "") == text


def test_global_indices_and_section_titles() -> None:
    chunks = chunk_document(
        document(("A", "aaa"), ("B", "bbb")),
        chunk_size=10,
        overlap=0,
        max_chunks=10,
    )
    assert [chunk.index for chunk in chunks] == [0, 1]
    assert [chunk.section_title for chunk in chunks] == ["A", "B"]


def test_max_chunks_caps_output() -> None:
    text = "\n\n".join(f"段落{i}" for i in range(50))
    chunks = chunk_document(document((None, text)), chunk_size=3, overlap=0, max_chunks=5)
    assert len(chunks) == 5


def test_overlap_never_exceeds_chunk_size() -> None:
    chunks = chunk_document(
        document((None, "甲" * 20 + "\n\n" + "乙" * 20)),
        chunk_size=10,
        overlap=4,
        max_chunks=20,
    )
    assert chunks
    assert all(chunk.char_count <= 10 for chunk in chunks)


def test_table_header_repeated() -> None:
    table = ParsedDocument(
        (ParsedSection("表格", "列1 | 列2\nA | 1\nB | 2\nC | 3", kind="table"),),
        "test",
    )
    chunks = chunk_document(table, chunk_size=20, overlap=0, max_chunks=10)
    assert len(chunks) >= 2
    assert all(chunk.content.startswith("列1 | 列2") for chunk in chunks)
    assert all(" | " in chunk.content for chunk in chunks)


def test_heading_path_kept_in_metadata() -> None:
    parsed = ParsedDocument(
        (
            ParsedSection(
                "第二节",
                "正文",
                heading_path=("第一章", "第二节"),
            ),
        ),
        "test",
    )
    chunks = chunk_document(parsed, chunk_size=100, overlap=0, max_chunks=10)
    assert chunks[0].metadata["heading_path"] == ["第一章", "第二节"]
    assert chunks[0].metadata["kind"] == "text"


def test_whitespace_only_sections_skipped() -> None:
    chunks = chunk_document(
        document((None, "   \n\n  "), ("有内容", "abc")),
        chunk_size=10,
        overlap=0,
        max_chunks=10,
    )
    assert len(chunks) == 1
    assert chunks[0].content == "abc"
