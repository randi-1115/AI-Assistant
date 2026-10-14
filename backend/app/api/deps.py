"""FastAPI 依赖。

第一阶段没有认证，get_current_user 直接返回固定的开发用户。但**所有业务
查询仍然经过它拿 user_id**，而不是把常量散落在各处——将来换成真实认证时，
只需要改这一个函数。
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User
from app.repositories.user_repo import UserRepository


async def get_current_user(db: AsyncSession = Depends(get_db)) -> User:
    settings = get_settings()
    repo = UserRepository(db)
    user = await repo.get_by_email(settings.dev_user_email)
    if user is None:
        user = await repo.get_or_create(
            email=settings.dev_user_email,
            display_name=settings.dev_user_display_name,
        )
        await db.commit()
    return user


async def get_current_user_id(user: User = Depends(get_current_user)) -> int:
    return user.id
