"""解析器：编码、格式校验、文件名清洗。"""

from __future__ import annotations

import pytest

from app.rag.parser_base import InvalidFilenameError, ParseError, UnsupportedFormatError
from app.rag.parsers import (
    decode_text,
    parse_document,
    sanitize_filename,
    sniff_file,
)


def test_decode_utf8_with_bom() -> None:
    assert decode_text("﻿你好".encode("utf-8")) == "你好"


def test_decode_gb18030() -> None:
    assert decode_text("中文内容".encode("gb18030")) == "中文内容"


def test_decode_utf16_bom() -> None:
    assert decode_text("hello 世界".encode("utf-16")) == "hello 世界"


def test_markdown_headings(tmp_path) -> None:
    path = tmp_path / "a.md"
    path.write_text(
        "# 标题一\n\n正文一\n\n```python\n# 这不是标题\n```\n\n## 标题二\n\n正文二",
        encoding="utf-8",
    )
    document = parse_document(path, extension=".md", max_chars=10000)
    assert [section.title for section in document.sections] == ["标题一", "标题二"]
    assert "# 这不是标题" in document.sections[0].text


def test_plain_text_single_section(tmp_path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("第一段\n\n第二段", encoding="utf-8")
    document = parse_document(path, extension=".txt", max_chars=10000)
    assert len(document.sections) == 1
    assert document.sections[0].title is None


def test_max_chars_truncates(tmp_path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("x" * 1000, encoding="utf-8")
    document = parse_document(path, extension=".txt", max_chars=100)
    assert len(document.text) <= 100


def test_empty_file_fails(tmp_path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(b"")
    with pytest.raises(ParseError):
        parse_document(path, extension=".txt", max_chars=1000)


def test_binary_masquerading_as_text_rejected(tmp_path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(b"\x00\x01\x02\x03" * 100)
    with pytest.raises(UnsupportedFormatError):
        parse_document(path, extension=".txt", max_chars=1000)


def test_pdf_wrong_magic_rejected(tmp_path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"not a pdf")
    with pytest.raises(UnsupportedFormatError):
        sniff_file(path, ".pdf")


def test_docx_invalid_container_rejected(tmp_path) -> None:
    path = tmp_path / "a.docx"
    path.write_bytes(b"not a zip")
    with pytest.raises(UnsupportedFormatError):
        sniff_file(path, ".docx")


def test_docx_headings_and_paragraphs(tmp_path) -> None:
    from docx import Document

    path = tmp_path / "a.docx"
    document = Document()
    document.add_heading("标题一", level=1)
    document.add_paragraph("正文内容")
    document.save(path)

    parsed = parse_document(path, extension=".docx", max_chars=10000)
    assert any(section.title == "标题一" for section in parsed.sections)
    assert "正文内容" in parsed.text


def test_docx_heading_only_is_searchable(tmp_path) -> None:
    from docx import Document

    path = tmp_path / "headings.docx"
    document = Document()
    document.add_heading("第一章 神经网络", level=1)
    document.add_heading("1.1 反向传播", level=2)
    document.save(path)

    parsed = parse_document(path, extension=".docx", max_chars=10000)

    assert any("第一章 神经网络" in section.text for section in parsed.sections)
    assert any(
        section.heading_path == ("第一章 神经网络", "1.1 反向传播")
        for section in parsed.sections
    )


def test_docx_table_keeps_order_and_heading(tmp_path) -> None:
    from docx import Document

    path = tmp_path / "table.docx"
    document = Document()
    document.add_heading("实验设置", level=1)
    document.add_paragraph("下面是超参数。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "名称"
    table.cell(0, 1).text = "值"
    table.cell(1, 0).text = "学习率"
    table.cell(1, 1).text = "0.01"
    document.save(path)

    parsed = parse_document(path, extension=".docx", max_chars=10000)

    table_sections = [section for section in parsed.sections if section.kind == "table"]
    assert table_sections
    table_section = table_sections[0]
    assert table_section.title == "实验设置（表格）"
    assert "名称 | 值" in table_section.text
    assert "学习率 | 0.01" in table_section.text
    assert table_section.heading_path == ("实验设置",)
    assert any("下面是超参数" in section.text for section in parsed.sections)


def test_truncation_is_marked(tmp_path) -> None:
    path = tmp_path / "long.txt"
    path.write_text("这是一句话。" * 200, encoding="utf-8")

    parsed = parse_document(path, extension=".txt", max_chars=50)

    assert parsed.truncated is True
    assert any(section.truncated for section in parsed.sections)


def test_pdf_with_no_text_reports_parse_error(tmp_path) -> None:
    from pypdf import PdfWriter

    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ParseError):
        parse_document(path, extension=".pdf", max_chars=10000)


def test_image_rejected(tmp_path) -> None:
    path = tmp_path / "a.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(UnsupportedFormatError):
        parse_document(path, extension=".png", max_chars=1000)


def test_sanitize_filename_strips_path_and_nfc() -> None:
    name, extension = sanitize_filename(r"..\..\evil.TXT")
    assert name == "evil.txt"
    assert extension == ".txt"


def test_sanitize_filename_rejects_missing_extension() -> None:
    with pytest.raises(InvalidFilenameError):
        sanitize_filename("README")


def test_sanitize_filename_truncates_long_stem() -> None:
    name, _ = sanitize_filename("a" * 400 + ".txt")
    assert len(name) <= 255
    assert name.endswith(".txt")
