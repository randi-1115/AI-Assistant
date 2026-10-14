"""上下文裁剪的边界行为。纯函数，不需要数据库和网络。"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.context_builder import build_context


@dataclass
class FakeMessage:
    role: str
    content: str
    status: str = "complete"


def history(*specs: tuple[str, str]) -> list[FakeMessage]:
    return [FakeMessage(role=role, content=content) for role, content in specs]


def roles(context) -> list[str]:
    return [message.role for message in context]


def test_system_prompt_is_first_and_empty_history_only_yields_it():
    context = build_context([], system_prompt="SYS", max_messages=20, max_chars=8000)
    assert len(context) == 1
    assert context[0].role == "system"
    assert context[0].content == "SYS"


def test_keeps_chronological_order():
    context = build_context(
        history(("user", "一"), ("assistant", "二"), ("user", "三")),
        system_prompt="SYS",
        max_messages=20,
        max_chars=8000,
    )
    assert roles(context) == ["system", "user", "assistant", "user"]
    assert [m.content for m in context[1:]] == ["一", "二", "三"]


def test_max_messages_keeps_the_most_recent():
    messages = history(
        ("user", "m1"), ("assistant", "m2"), ("user", "m3"), ("assistant", "m4"), ("user", "m5")
    )
    context = build_context(messages, system_prompt="SYS", max_messages=3, max_chars=8000)
    assert [m.content for m in context[1:]] == ["m3", "m4", "m5"]


def test_max_chars_drops_oldest_first():
    # 两轮完整问答，预算只够最后一轮。
    messages = history(
        ("user", "a" * 100),
        ("assistant", "b" * 100),
        ("user", "c" * 10),
        ("assistant", "d" * 10),
    )
    # 预算 30 只装得下最后两条（共 20 字符），第一轮被整体丢掉。
    context = build_context(messages, system_prompt="SYS", max_messages=20, max_chars=30)
    assert [m.content for m in context[1:]] == ["c" * 10, "d" * 10]


def test_assistant_orphaned_by_budget_trimming_is_dropped():
    messages = history(("user", "a" * 100), ("assistant", "b" * 100), ("user", "c" * 10))
    context = build_context(messages, system_prompt="SYS", max_messages=20, max_chars=150)
    # 预算是 150，装上 b(100)+c(10) 就满了；但 b 的提问 a 已被裁掉，
    # 保留一个「没有问题的回答」对模型没有意义，所以连同 b 一起丢掉。
    assert [m.content for m in context[1:]] == ["c" * 10]


def test_last_message_is_kept_even_when_it_alone_exceeds_budget():
    messages = history(("user", "old"), ("assistant", "old reply"), ("user", "x" * 500))
    context = build_context(messages, system_prompt="SYS", max_messages=20, max_chars=10)
    # 最后一条是唯一留下的——模型必须有东西可回答。
    assert [m.content for m in context[1:]] == ["x" * 500]


def test_orphan_assistant_head_is_removed():
    # 裁剪后首条成了 assistant（它配对的 user 被裁掉了），要连同它一起丢掉。
    messages = history(
        ("user", "u1"), ("assistant", "a1"), ("user", "u2"), ("assistant", "a2")
    )
    context = build_context(messages, system_prompt="SYS", max_messages=3, max_chars=8000)
    assert roles(context) == ["system", "user", "assistant"]
    assert [m.content for m in context[1:]] == ["u2", "a2"]


def test_empty_content_messages_are_filtered_out():
    messages = history(
        ("user", "问题"),
        ("assistant", ""),  # 流式失败留下的空占位
        ("assistant", "   "),  # 只有空白
        ("user", "追问"),
    )
    context = build_context(messages, system_prompt="SYS", max_messages=20, max_chars=8000)
    assert [m.content for m in context[1:]] == ["问题", "追问"]


def test_everything_filtered_out_leaves_only_system_prompt():
    context = build_context(
        history(("assistant", ""), ("assistant", "  ")),
        system_prompt="SYS",
        max_messages=20,
        max_chars=8000,
    )
    assert roles(context) == ["system"]
