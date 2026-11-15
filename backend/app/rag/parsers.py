"""文件解析：PDF / DOCX / TXT / Markdown / 常见代码文本。

设计原则：
- 不信任扩展名：先看魔数/容器结构，必要时做二进制启发式。
- 不做有损解码：UTF-16 BOM → UTF-8 → GB18030，全部失败就报错。
- 解析是同步阻塞的，调用方必须放到线程里跑（FileService 用 anyio.to_thread）。
- 图片在阶段 2 明确不支持文本问答，上传阶段直接拒绝。
"""

from __future__ import annotations

import mimetypes
import re
import unicodedata
import zipfile
from dataclasses import replace
from pathlib import Path

from app.rag.parser_base import (
    InvalidFilenameError,
    ParsedDocument,
    ParsedSection,
    ParseError,
    UnsupportedFormatError,
)

MAX_PDF_PAGES = 300
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000
MAX_DOCX_RATIO = 100

TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".json",
        ".yaml", ".yml", ".toml", ".ini", ".xml", ".html", ".htm",
        ".css", ".scss", ".less", ".js", ".jsx", ".ts", ".tsx", ".vue",
        ".py", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs",
        ".rb", ".php", ".swift", ".kt", ".kts", ".scala", ".sh", ".bash",
        ".sql", ".tex",
    }
)
MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def normalize_extension(extension: str) -> str:
    ext = extension.strip().lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    return ext


def is_image_extension(extension: str) -> bool:
    return normalize_extension(extension) in IMAGE_EXTENSIONS


def ensure_supported_extension(extension: str) -> None:
    ext = normalize_extension(extension)
    if ext in IMAGE_EXTENSIONS:
        raise UnsupportedFormatError("图片暂不支持文本问答，后续阶段再开放")
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(f"不支持的文件类型：{ext}")


def sanitize_filename(raw: str | None) -> tuple[str, str]:
    """只保留 basename，返回 (安全文件名, 小写扩展名)。

    用户文件名永远不会参与磁盘路径拼接，这里主要是为了展示安全与长度约束。
    """
    if not raw or not raw.strip():
        raise InvalidFilenameError("文件名为空")
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = unicodedata.normalize("NFC", name)
    name = _CONTROL_CHARS.sub("", name).strip()
    if name in ("", ".", ".."):
        raise InvalidFilenameError("文件名为空")
    suffix = Path(name).suffix.lower()
    if not suffix or suffix == ".":
        raise InvalidFilenameError("文件缺少扩展名")
    stem = name[: -len(suffix)].strip(" .") or "upload"
    max_stem = max(1, 255 - len(suffix))
    return f"{stem[:max_stem]}{suffix}", suffix


def decode_text(data: bytes) -> str:
    """严格解码：UTF-16 BOM → UTF-8(含 BOM) → GB18030。"""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError("无法识别文本编码（已尝试 UTF-8 与 GB18030）")


def _looks_binary(head: bytes) -> bool:
    if not head:
        return False
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    if b"\x00" in head:
        return True
    printable = sum(
        1
        for byte in head
        if byte in (9, 10, 12, 13) or 32 <= byte <= 126 or byte >= 128
    )
    return printable / len(head) < 0.90


def _mime_for(extension: str) -> str:
    guessed, _ = mimetypes.guess_type(f"upload{extension}")
    return guessed or "text/plain"


def _check_docx_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_DOCX_ENTRIES:
                raise UnsupportedFormatError("DOCX 内部条目过多")
            total = sum(info.file_size for info in infos)
            if total > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise UnsupportedFormatError("DOCX 解压后体积过大")
            for info in infos:
                if info.compress_size and info.file_size / info.compress_size > MAX_DOCX_RATIO:
                    raise UnsupportedFormatError("DOCX 压缩比异常")
            names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise UnsupportedFormatError("DOCX 容器结构不合法") from exc

    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise UnsupportedFormatError("DOCX 缺少必要的文档结构")


def sniff_file(path: Path, extension: str) -> str:
    """校验真实文件类型，返回 MIME。不匹配时抛 UnsupportedFormatError。"""
    ext = normalize_extension(extension)
    ensure_supported_extension(ext)

    if ext == ".pdf":
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise UnsupportedFormatError("文件内容不是有效的 PDF")
        return "application/pdf"

    if ext == ".docx":
        _check_docx_zip(path)
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    with path.open("rb") as handle:
        head = handle.read(8192)
    if _looks_binary(head):
        raise UnsupportedFormatError("文件内容不是可解析的文本")
    return _mime_for(ext)


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    window = text[:max_chars]
    cut = max(window.rfind(char) for char in "。！？.!?\n")
    if cut >= max_chars // 2:
        window = window[: cut + 1]
    value = window.rstrip()
    marker = "\n[内容已截断]"
    if len(value) + len(marker) <= max_chars:
        value += marker
    return value, True


def _limit_sections(sections: list[ParsedSection], max_chars: int) -> list[ParsedSection]:
    if max_chars <= 0:
        return []
    result: list[ParsedSection] = []
    remaining = max_chars
    for section in sections:
        text = section.text.strip()
        if not text:
            continue
        if remaining <= 0:
            result.append(replace(section, text="", truncated=True))
            break
        truncated = section.truncated
        if len(text) > remaining:
            text, was_truncated = _truncate_text(text, remaining)
            truncated = truncated or was_truncated
        result.append(replace(section, text=text, truncated=truncated))
        remaining -= len(text)
    return result


def _parse_plain(path: Path, parser_name: str = "text") -> ParsedDocument:
    text = decode_text(path.read_bytes()).replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ParseError("未提取到可用文本（空文件）")
    return ParsedDocument((ParsedSection(None, text),), parser_name)


def _parse_markdown(path: Path) -> ParsedDocument:
    text = decode_text(path.read_bytes()).replace("\r\n", "\n").replace("\r", "\n")
    sections: list[ParsedSection] = []
    heading_stack: list[tuple[int, str]] = []
    title: str | None = None
    heading_path: tuple[str, ...] = ()
    buffer: list[str] = []
    in_fence = False

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if not body and title:
            body = title  # 标题-only 文档也要可检索
        if body:
            sections.append(ParsedSection(title, body, heading_path))
        buffer.clear()

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            buffer.append(line)
            continue
        match = None if in_fence else _HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            heading = match.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            if heading:
                heading_stack.append((level, heading))
            title = heading or None
            heading_path = tuple(item[1] for item in heading_stack)
            continue
        buffer.append(line)
    flush()

    if not sections:
        if not text.strip():
            raise ParseError("未提取到可用文本（空文件）")
        sections.append(ParsedSection(None, text))
    return ParsedDocument(tuple(sections), "markdown")


def _heading_level(style_name: str | None) -> int | None:
    if not style_name:
        return None
    lowered = style_name.strip().lower()
    if not (lowered.startswith("heading") or lowered.startswith("标题")):
        return None
    match = re.search(r"(\d+)", lowered)
    return int(match.group(1)) if match else 1


def _iter_docx_blocks(document):
    if hasattr(document, "iter_inner_content"):
        yield from document.iter_inner_content()
        return
    for paragraph in document.paragraphs:
        yield paragraph
    for table in document.tables:
        yield table


def _docx_table_text(table) -> tuple[str, str]:
    rows = []
    for row in table.rows:
        rows.append(" | ".join(cell.text.strip() for cell in row.cells).strip())
    rows = [row for row in rows if row]
    if not rows:
        return "", ""
    header = rows[0]
    body = "\n".join(rows)
    return body, header


def _parse_docx(path: Path) -> ParsedDocument:
    _check_docx_zip(path)
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - 依赖已在 requirements
        raise ParseError("DOCX 解析依赖未安装") from exc

    try:
        document = Document(str(path))
    except Exception as exc:
        raise ParseError(f"DOCX 解析失败：{exc}") from exc

    sections: list[ParsedSection] = []
    heading_stack: list[tuple[int, str]] = []
    title: str | None = None
    heading_path: tuple[str, ...] = ()
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if not body and title:
            body = title  # 标题-only 文档也要可检索
        if body:
            sections.append(ParsedSection(title, body, heading_path))
        buffer.clear()

    for block in _iter_docx_blocks(document):
        if hasattr(block, "rows"):
            flush()
            body, header = _docx_table_text(block)
            if body:
                table_title = f"{title}（表格）" if title else "表格"
                sections.append(
                    ParsedSection(table_title, body, heading_path, kind="table")
                )
            continue

        text = (getattr(block, "text", "") or "").strip()
        style = getattr(block, "style", None)
        style_name = getattr(style, "name", None) if style is not None else None
        level = _heading_level(style_name)
        if level is not None:
            flush()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            if text:
                heading_stack.append((level, text))
            title = text or None
            heading_path = tuple(item[1] for item in heading_stack)
            continue
        if text:
            buffer.append(text)
    flush()

    if not any(section.text.strip() for section in sections):
        raise ParseError("未提取到可用文本（可能是空文档或纯图片 DOCX）")
    return ParsedDocument(tuple(sections), "docx")


def _parse_pdf(path: Path) -> ParsedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 依赖已在 requirements
        raise ParseError("PDF 解析依赖未安装") from exc

    truncated = False
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                decrypted = reader.decrypt("")
            except Exception as exc:
                raise ParseError("PDF 已加密，无法提取文本") from exc
            if not decrypted:
                raise ParseError("PDF 已加密，无法提取文本")

        sections: list[ParsedSection] = []
        for index, page in enumerate(reader.pages):
            if index >= MAX_PDF_PAGES:
                truncated = True
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                # 单页失败不影响其他页；全空时由下面的检查报错。
                text = ""
            text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
            if text:
                sections.append(
                    ParsedSection(f"第 {index + 1} 页", text, kind="page")
                )
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"PDF 解析失败：{exc}") from exc

    if not sections:
        raise ParseError("未提取到可用文本（可能是扫描版 PDF）")
    return ParsedDocument(tuple(sections), "pdf", truncated=truncated)


def parse_document(path: Path, *, extension: str, max_chars: int) -> ParsedDocument:
    """统一入口：校验真实类型后按扩展名分发，并做字符预算截断。"""
    ext = normalize_extension(extension)
    ensure_supported_extension(ext)
    mime_type = sniff_file(path, ext)

    if ext == ".pdf":
        document = _parse_pdf(path)
    elif ext == ".docx":
        document = _parse_docx(path)
    elif ext in MARKDOWN_EXTENSIONS:
        document = _parse_markdown(path)
    else:
        document = _parse_plain(path, "text")

    sections = _limit_sections(list(document.sections), max_chars)
    if not any(section.text.strip() for section in sections):
        raise ParseError("未提取到可用文本（可能是扫描版或空文件）")
    truncated = document.truncated or any(section.truncated for section in sections)
    return ParsedDocument(
        tuple(sections), document.parser_name, mime_type, truncated=truncated
    )
