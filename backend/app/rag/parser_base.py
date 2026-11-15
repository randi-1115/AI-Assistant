"""解析器接口与规范化结果。

解析器把不同格式统一成「按节组织的纯文本」。section 用来保留页码/标题，
检索结果与提示词里都能带上它作为来源线索。P1.5 增加 heading_path/kind，
让标题层级和表格进入检索文本，但不改变数据库结构。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SectionKind = Literal["text", "table", "page"]


@dataclass(frozen=True, slots=True)
class ParsedSection:
    title: str | None
    text: str
    heading_path: tuple[str, ...] = ()
    kind: SectionKind = "text"
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    sections: tuple[ParsedSection, ...]
    parser_name: str
    mime_type: str = "text/plain"
    truncated: bool = False

    @property
    def text(self) -> str:
        return "\n\n".join(section.text for section in self.sections if section.text.strip())


class ParseError(Exception):
    """格式合法但内容无法解析（空文本、损坏、编码无法识别等）。"""


class UnsupportedFormatError(ParseError):
    """扩展名或真实文件类型不受支持，上传阶段就应拒绝。"""


class InvalidFilenameError(ParseError):
    """文件名缺失、为空或没有可用扩展名。"""
