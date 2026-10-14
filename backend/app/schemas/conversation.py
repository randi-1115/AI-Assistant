"""会话相关的请求/响应契约。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.conversation import DEFAULT_CONVERSATION_TITLE

MAX_TITLE_LENGTH = 200


class ConversationCreate(BaseModel):
    title: str | None = Field(
        default=None, max_length=MAX_TITLE_LENGTH, description="省略时使用「新对话」"
    )

    def resolved_title(self) -> str:
        title = (self.title or "").strip()
        return title or DEFAULT_CONVERSATION_TITLE


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
