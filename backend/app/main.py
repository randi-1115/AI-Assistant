"""FastAPI 入口。

这里组装：日志、请求 ID、CORS、统一异常处理、路由，以及启动时的数据准备。

请求日志刻意用**纯 ASGI 中间件**而不是 Starlette 的 BaseHTTPMiddleware：
后者会包一层自己的 receive 通道，在流式响应下可能让 `request.is_disconnected()`
表现异常——而「停止生成」正依赖它。纯 ASGI 中间件只包 send，不碰 receive。
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders

from app.api.v1.router import api_router
from app.api.v1.endpoints import health
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, new_request_id, set_request_id
from app.db.seed import ensure_dev_user, fail_orphan_streaming_messages
from app.db.session import dispose_engine

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = b"x-request-id"


class RequestContextMiddleware:
    """为每个请求分配 request_id，记访问日志，并把 id 回写到响应头。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = ""
        for name, value in scope.get("headers") or []:
            if name == REQUEST_ID_HEADER:
                request_id = value.decode("latin-1")
                break
        request_id = request_id or new_request_id()
        set_request_id(request_id)

        method = scope.get("method", "-")
        path = scope.get("path", "-")
        started_at = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            # 流式响应会在这里才收尾，所以耗时是整段流的时长。
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.info("%s %s -> %s (%.0fms)", method, path, status_code, elapsed_ms)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "启动 %s v%s，env=%s，LLM_PROVIDER=%s",
        settings.app_name,
        settings.app_version,
        settings.app_env,
        settings.llm_provider,
    )

    # 数据准备失败（最常见的是 MySQL 没启动）不能让整个应用起不来——
    # /health 会如实报告数据库不可用，方便定位问题。
    try:
        await fail_orphan_streaming_messages()
        await ensure_dev_user()
    except Exception as exc:  # noqa: BLE001
        logger.warning("启动时数据准备失败，服务仍会启动：%s", exc)

    yield

    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI Assistant API",
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        # 前端直连（不走 Vite 代理）时要能读到这个头，才能把错误和后端日志对上。
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    # /health 挂在根路径（运维探活习惯），业务接口统一在 API_V1_PREFIX 下。
    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
