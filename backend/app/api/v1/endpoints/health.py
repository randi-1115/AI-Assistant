"""健康检查。

数据库探活单独返回，不因为 DB 挂了就把整个接口变成 500——否则无法区分
「进程活着但依赖不可用」和「进程本身就起不来」。响应里不出现任何凭据。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    settings = get_settings()
    try:
        await db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001 - 探活失败的原因只进日志
        logger.warning("数据库探活失败：%s", exc)
        database = "unavailable"

    return {
        "status": "ok" if database == "ok" else "degraded",
        "version": settings.app_version,
        "dependencies": {"database": database},
    }
