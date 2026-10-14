"""异步数据库引擎与会话工厂。

注意：engine 在模块导入时创建，但不会立刻建连接，所以 MySQL 没启动时
import 也不会失败——真正报错会发生在第一次查询，由统一错误处理兜住。

SSE 流内部的落库不要用 get_db 注入的 session（请求被取消时它可能已经被
拆解），要用独立的 SessionLocal()，见 ChatService._finalize_safely。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    # 开发机 MySQL 的 wait_timeout 可能较短，长空闲后首个连接会 "server has gone away"。
    pool_pre_ping=True,
    pool_recycle=3600,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个会话。"""
    async with SessionLocal() as session:
        yield session


async def dispose_engine() -> None:
    """应用关闭时释放连接池。"""
    await engine.dispose()
