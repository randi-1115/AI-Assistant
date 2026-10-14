"""v1 路由汇总。

P0 只挂 system / conversations / chat 三组。files、memories、settings 的占位
endpoint 文件保持原样，等各自阶段实现后再在这里 include。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import chat, conversations, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(conversations.router)
api_router.include_router(chat.router)
