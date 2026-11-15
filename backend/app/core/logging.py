"""日志配置与请求 ID 上下文。

每个请求都会被分配一个 request_id(见 main.py 的中间件)，写入 ContextVar,
再由日志 Filter 注入每条日志，最后由统一错误处理返回给前端。
这样前端看到的错误提示能和后端日志一一对应，同时不必暴露内部细节。

注意：这里刻意不提供打印请求体/请求头的便捷方法——API Key、Authorization
和完整 prompt 都不应该出现在日志里。
"""

from __future__ import annotations

import logging
import logging.config
import uuid
from contextvars import ContextVar
from typing import Any

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")# 请求 ID 上下文变量

LOG_FORMAT = "%(asctime)s %(levelname)-5s [%(request_id)s] %(name)s: %(message)s"


def new_request_id() -> str:
    """生成一个短请求 ID。"""
    return uuid.uuid4().hex[:16]


def set_request_id(request_id: str) -> None:
    _request_id_var.set(request_id)


def get_request_id() -> str:
    return _request_id_var.get()


class RequestIdFilter(logging.Filter):
    """把当前请求 ID 注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_id": {"()": RequestIdFilter}},
            "formatters": {"default": {"format": LOG_FORMAT, "datefmt": "%H:%M:%S"}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "filters": ["request_id"],
                }
            },
            "root": {"handlers": ["console"], "level": level.upper()},
            # uvicorn 的 access log 与本项目的请求日志重复，降到 WARNING 避免刷屏。
            "loggers": {
                "uvicorn.access": {"level": "WARNING", "propagate": True},
                "sqlalchemy.engine": {"level": "WARNING"},
            },
        }
    )


def log_extra(**kwargs: Any) -> dict[str, Any]:
    """预留的结构化日志扩展点，目前仅返回 extra 字典。"""
    return kwargs
