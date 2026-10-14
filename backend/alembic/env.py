"""Alembic 运行环境（异步引擎版）。

本目录只有 versions/ 子目录，没有 Alembic 自动生成的 env.py —— 因为目录非空，
`alembic init` 会直接报错。这个文件是手写的。

命令都在 backend/ 目录下执行（alembic.ini 里配了 prepend_sys_path = .）：
    alembic revision --autogenerate -m "..."
    alembic upgrade head
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.models  # noqa: F401  让所有模型注册进 Base.metadata
from app.core.config import get_settings
from app.db.base import Base

# 从 Settings 注入连接串，避免把密码写进 alembic.ini。
settings = get_settings()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)

# app/db/base.py 底部已 import 全部 P0 模型，autogenerate 才能看到这些表。
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # 让 autogenerate 能察觉列类型和 server_default 的变化，否则改了不报警。
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # 迁移是一次性任务，不需要连接池。
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
