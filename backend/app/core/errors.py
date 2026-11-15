"""统一错误类型与全局异常处理。

所有错误响应都是同一结构（见 docs/API-SPEC.md）：

    {"error": {"code": "...", "message": "...", "request_id": "..."}}

设计要点：
- 业务层只抛 AppError 及其子类，不直接构造 JSONResponse。
- LLM 供应商的异常由 provider 映射成本模块的 LLM* 类型后再向上抛，
  上游原始响应正文只进日志、不进响应，避免泄露密钥或内部信息。
- 兜底 handler 对未知异常返回 500，响应体不含堆栈。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_request_id

logger = logging.getLogger(__name__)


class AppError(Exception):
    """所有可预期业务错误的基类。"""

    code: str = "internal_error"
    status_code: int = 500
    message: str = "服务器内部错误"

    def __init__(self, message: str | None = None, *, status_code: int | None = None) -> None:
        self.message = message or type(self).message
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)


# --- 通用 ---
class NotFoundError(AppError):
    code = "not_found"
    status_code = 404
    message = "资源不存在"


class ConversationNotFound(NotFoundError):
    code = "conversation_not_found"
    message = "会话不存在"


class ValidationAppError(AppError):
    code = "validation_error"
    status_code = 422
    message = "请求参数不合法"


# --- 文件上传 ---
class FileNotFound(NotFoundError):
    code = "file_not_found"
    message = "文件不存在"


class FileTooLarge(AppError):
    code = "file_too_large"
    status_code = 413
    message = "文件超过大小限制"


class UnsupportedFileType(AppError):
    code = "unsupported_file_type"
    status_code = 415
    message = "不支持的文件类型"


class InvalidFileContent(AppError):
    code = "invalid_file_content"
    status_code = 415
    message = "文件内容与扩展名不符"


class InvalidFileName(ValidationAppError):
    code = "invalid_file_name"
    message = "文件名不合法"


class TooManyAttachments(ValidationAppError):
    code = "too_many_attachments"
    message = "单条消息最多附带 10 个文件"


class StorageFailure(AppError):
    code = "storage_error"
    status_code = 500
    message = "文件存储失败"


# --- LLM ---
class LLMError(AppError):
    code = "llm_error"
    status_code = 502
    message = "模型服务暂时不可用"


class LLMAuthError(LLMError):
    code = "llm_auth_failed"
    message = "模型服务鉴权失败，请检查后端配置的 API Key"


class LLMRateLimitError(LLMError):
    code = "llm_rate_limited"
    status_code = 429
    message = "模型服务请求过于频繁，请稍后再试"


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    status_code = 504
    message = "模型服务响应超时"


class LLMUpstreamError(LLMError):
    code = "llm_upstream_error"
    message = "模型服务返回了错误"


class CapabilityNotSupported(LLMError):
    code = "capability_not_supported"
    status_code = 400
    message = "当前模型不支持该能力"


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """统一的错误响应。

    响应头里也要带 X-Request-ID：未处理的异常由 Starlette 的
    ServerErrorMiddleware 处理，它在本项目的请求中间件**外层**，直接发响应，
    会绕过中间件设置响应头的那一步。在 handler 里补上，保证任何错误响应
    都能用同一个 id 去后端日志里定位。
    """
    request_id = get_request_id()
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
        headers={"X-Request-ID": request_id},
    )


async def app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    # 可预期错误用 warning；500 级别的内部错误交给兜底 handler。
    logger.warning("业务错误 code=%s message=%s", exc.code, exc.message)
    return error_response(exc.status_code, exc.code, exc.message)


def _summarize_validation_error(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "请求参数不合法"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    detail = first.get("msg", "取值不合法")
    return f"参数 {location or '请求体'} 不合法：{detail}"


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return error_response(422, "validation_error", _summarize_validation_error(exc))


async def http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """让框架自己抛的 HTTPException（404 路由、405 方法不允许等）也走统一信封。"""
    assert isinstance(exc, StarletteHTTPException)
    return error_response(exc.status_code, f"http_{exc.status_code}", str(exc.detail))


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    # 堆栈只进日志，不进响应体。
    logger.exception("未处理异常：%s", exc)
    return error_response(500, "internal_error", "服务器内部错误")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
