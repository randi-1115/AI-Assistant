"""启动时的数据准备与清理。

两件事都在 lifespan 里跑，且都**不能导致应用启动失败**：
  1. 确保固定的开发用户存在（第一阶段没有登录）
  2. 把上次异常退出遗留的 streaming 消息标成 failed

注意：这是本地单用户开发模式的做法，不等同于已经完成多用户安全认证。
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


async def ensure_dev_user() -> int | None:
    settings = get_settings()
    async with SessionLocal() as session:
        user = await UserRepository(session).get_or_create(
            email=settings.dev_user_email,
            display_name=settings.dev_user_display_name,
        )
        await session.commit()
        logger.info("开发用户就绪 id=%s email=%s", user.id, user.email)
        return user.id


async def fail_orphan_streaming_messages() -> int:
    """进程刚启动时不可能有正在进行的流，所以 streaming 一定是孤儿。"""
    async with SessionLocal() as session:
        count = await MessageRepository(session).fail_all_streaming()
        await session.commit()
    if count:
        logger.warning("把 %s 条遗留的 streaming 消息标记为 failed", count)
    return count


async def fail_orphan_parsing_attachments() -> int:
    """同步解析链路里，进程重启时不可能还有正在解析的附件。"""
    async with SessionLocal() as session:
        count = await AttachmentRepository(session).fail_incomplete()
        await session.commit()
    if count:
        logger.warning("把 %s 个未完成解析的附件标记为 failed", count)
    return count
