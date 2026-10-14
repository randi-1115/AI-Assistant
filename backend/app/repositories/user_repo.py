"""users 表的读写。

第一阶段只有一个固定的开发用户；补认证之后这个文件会扩展成真正的账号仓储。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_or_create(self, *, email: str, display_name: str) -> User:
        """幂等：开发用户已存在就直接返回。"""
        existing = await self.get_by_email(email)
        if existing is not None:
            return existing
        user = User(email=email, display_name=display_name, status="active")
        self.session.add(user)
        await self.session.flush()
        return user
