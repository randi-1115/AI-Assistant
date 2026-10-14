"""通用响应结构：错误信封与分页。

错误结构必须和 app/core/errors.py 里实际返回的一致，前端只按这一种形状解析。
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    """仅用于文档展示；实际错误响应由 core/errors.py 的 handler 构造。"""

    error: ErrorBody


class Page(BaseModel, Generic[T]):
    items: list[T]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)
    total: int = Field(ge=0)


class DeleteResult(BaseModel):
    """DELETE 用 204 空响应体；这个模型给需要回执的场景预留。"""

    deleted: bool
