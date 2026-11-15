"""导入全部 ORM 模型，让它们注册进 Base.metadata。

Alembic 的 env.py 只需要 `import app.models`，autogenerate 就能看到所有表。
新增模型时在这里补一行 import。
"""

from app.models.attachment import Attachment
from app.models.conversation import Conversation
from app.models.document_chunk import DocumentChunk
from app.models.message import Message
from app.models.user import User

__all__ = ["Attachment", "Conversation", "DocumentChunk", "Message", "User"]
