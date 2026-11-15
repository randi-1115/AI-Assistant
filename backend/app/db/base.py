"""SQLAlchemy 声明式基类与共享列类型。

项目只面向 MySQL，因此直接使用 mysql 方言的具体类型，不用 with_variant
做跨方言兼容。naming_convention 仍然保留：Alembic autogenerate 依赖它生成
稳定的索引/外键/约束名，否则同一份模型在不同机器上会产出不同的名字。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import MetaData, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# --- MySQL 列类型 ---
BIGINT_UNSIGNED = mysql.BIGINT(unsigned=True)
INT_UNSIGNED = mysql.INTEGER(unsigned=True)
# DATETIME(3)：毫秒精度，便于排查并发写入。
DATETIME_MS = mysql.DATETIME(fsp=3)
# 消息正文、文档块正文需要 LONGTEXT / MEDIUMTEXT；普通 TEXT 只有 64KB。
LONGTEXT = mysql.LONGTEXT()
MEDIUMTEXT = mysql.MEDIUMTEXT()

# 数据库当前时间，精确到毫秒。
NOW_MS = text("CURRENT_TIMESTAMP(3)")


class TimestampMixin:
    """created_at / updated_at 由数据库生成，避免应用与库时钟不一致。

    eager_defaults：这两列由数据库生成，flush() 后 SQLAlchemy 并不知道值，
    属性会停在 expired 状态。异步会话里再读会触发同步 IO，抛 MissingGreenlet
    （典型现场是 Pydantic 序列化时读 created_at）。打开后 SQLAlchemy 会在
    INSERT/UPDATE 之后立刻把服务端生成的列取回来。必须放在 mixin 上，
    子模型自己声明 __mapper_args__ 会覆盖掉这里。
    """

    __mapper_args__ = {"eager_defaults": True}

    created_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, server_default=NOW_MS, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, server_default=NOW_MS, onupdate=NOW_MS, nullable=False
    )


__all__ = [
    "Base",
    "BIGINT_UNSIGNED",
    "INT_UNSIGNED",
    "DATETIME_MS",
    "LONGTEXT",
    "MEDIUMTEXT",
    "TimestampMixin",
]
