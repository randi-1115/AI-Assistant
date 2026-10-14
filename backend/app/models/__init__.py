"""导入全部 ORM 模型，让它们注册进 Base.metadata。

Alembic 的 env.py 只需要 `import app.models`，autogenerate 就能看到所有表。
P1 阶段新增模型时，在这里补一行 import 即可。
"""

from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User

__all__ = ["Conversation", "Message", "User"]
