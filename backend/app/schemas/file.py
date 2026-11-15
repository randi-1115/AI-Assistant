"""文件上传/查询契约。字段与 docs/API-SPEC.md 保持一致。

注意：storage_key 与物理路径永远不出现在响应里。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ParseStatus = Literal["pending", "processing", "ready", "failed", "unsupported"]


class FileUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: int
    original_name: str
    size_bytes: int
    parse_status: ParseStatus
    supported_for_qa: bool
    error_message: str | None = None


class FileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: int
    conversation_id: int | None = None
    original_name: str
    mime_type: str | None = None
    extension: str
    size_bytes: int
    parse_status: ParseStatus
    supported_for_qa: bool
    error_message: str | None = None
    created_at: datetime
