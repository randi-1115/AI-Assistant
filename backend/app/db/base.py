"""SQLAlchemy 声明式基类与共享列类型。

这里的 naming_convention 不是装饰：Alembic autogenerate 依赖它生成稳定的
索引/外键/约束名，否则同一次迁移在不同机器上会产出不同的名字，很容易
在后续迁移里产生假 diff。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, MetaData, Text, text
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


# DB-SCHEMA 要求 BIGINT UNSIGNED 主键。with_variant 让同一份模型定义在
# 其他方言（如将来可能的 sqlite）上也能建表，不至于直接报错。
BIGINT_UNSIGNED = BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql")

# DATETIME(3)：毫秒精度。消息按 id 排序已经足够，但保留毫秒便于排查并发写入。
DATETIME_MS = mysql.DATETIME(fsp=3).with_variant(DateTime(), "sqlite")

# 消息正文用 LONGTEXT。MySQL 的 TEXT 只有 64KB，长回答会被静默截断。
LONGTEXT = Text().with_variant(mysql.LONGTEXT(), "mysql")

NOW_MS = text("CURRENT_TIMESTAMP(3)")


class TimestampMixin:
    """created_at / updated_at 由数据库生成，避免应用与库时钟不一致。"""

    # eager_defaults：这两列的值由数据库生成，flush() 之后 SQLAlchemy 并不知道
    # 它们是什么，属性会停在 expired 状态。异步会话里再去读就会触发一次同步 IO，
    # 抛 MissingGreenlet（典型现场是 Pydantic 序列化时读 created_at）。
    # 打开后 SQLAlchemy 会在 INSERT/UPDATE 之后立刻把这几个服务端生成的列取回来，
    # 序列化就不必再查库。注意必须放在 mixin 上，各模型自己声明 __mapper_args__
    # 会覆盖掉这里的设置。
    __mapper_args__ = {"eager_defaults": True}

    created_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, server_default=NOW_MS, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME_MS, server_default=NOW_MS, onupdate=NOW_MS, nullable=False
    )


# 模型向 Base.metadata 的注册放在 app/models/__init__.py。
# 刻意不在这里 import 模型：模型本身要 import 本模块的 Base，放这里会形成循环导入。

__all__ = [
    "Base",
    "BIGINT_UNSIGNED",
    "DATETIME_MS",
    "LONGTEXT",
    "TimestampMixin",
]
