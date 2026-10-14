"""消息响应契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str
    content: str
    status: str
    model_provider: str | None = None
    model_name: str | None = None
    metadata_json: dict[str, Any] | None = None
    created_at: datetime
