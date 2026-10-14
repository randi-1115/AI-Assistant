"""把数据库里的历史消息裁剪成要发给模型的消息列表。

这是纯函数，不碰数据库，因此可以脱离 MySQL 和网络单测——上下文裁剪是最容易
出边界 bug 的地方，必须能独立测。

关于 token 预算：这里用**字符数**做近似（中文大约 1 字 ≈ 1 token）。
PRD 第 8 节的要求只是「不要无限传送历史对话」，字符启发式足够；
P1 若引入真 tokenizer，替换点就在这个文件。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.ai.providers.base import ChatMessage, ROLE_SYSTEM, ROLE_USER


class HistoryMessage(Protocol):
    """历史消息只需要这三个属性，因此 ORM 对象和测试用的假对象都能传进来。"""

    role: str
    content: str
    status: str


def build_context(
    history: Sequence[HistoryMessage],
    *,
    system_prompt: str,
    max_messages: int,
    max_chars: int,
) -> list[ChatMessage]:
    """返回 [system] + 裁剪后的历史，始终按时间升序。

    裁剪顺序：
      1. 丢掉内容为空的消息——包括流式失败留下的空助手占位、被中断的空白回答。
         它们对模型没有任何信息量，还会让上下文出现连续两个 user 之类的问题。
      2. 只保留最近 max_messages 条。
      3. 从最旧端开始丢，直到总字符数进入 max_chars 预算。**但最后一条永远保留**,
         否则模型没有可回答的问题。
      4. 修正「孤头」：如果保留下来的第一条不是 user，说明它配对的提问已被裁掉，
         继续从头丢，直到首条是 user（或全空）。
    """

    usable = [message for message in history if (message.content or "").strip()]

    if max_messages > 0:
        usable = usable[-max_messages:]

    selected: list[HistoryMessage] = []
    total_chars = 0
    for message in reversed(usable):
        length = len(message.content)
        # selected 非空才允许 break —— 保证最后一条无论多长都留下。
        if selected and total_chars + length > max_chars:
            break
        selected.append(message)
        total_chars += length
    selected.reverse()

    while selected and selected[0].role != ROLE_USER:
        selected.pop(0)

    context = [ChatMessage(role=ROLE_SYSTEM, content=system_prompt)]
    context.extend(ChatMessage(role=m.role, content=m.content) for m in selected)
    return context
