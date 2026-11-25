"""注册表：白名单、参数校验、超时、取消与统一错误。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import pytest
from pydantic import Field

from app.tools.base import Tool, ToolArgs, ToolContext, ToolError, ToolResult, ToolUnavailable
from app.tools.registry import TRUNCATION_MARKER, ToolExecution, ToolRegistry

CONTEXT = ToolContext(user_id=1, request_id="req-test")
_UNSET = object()


class FakeArgs(ToolArgs):
    text: str = Field(min_length=1)


class FakeTool(Tool[FakeArgs]):
    name = "fake"
    description = "测试工具"
    args_model = FakeArgs

    def __init__(
        self,
        *,
        result: ToolResult | None = None,
        error: BaseException | None = None,
        return_value: object = _UNSET,
        delay: float = 0.0,
        available: bool = True,
        reason: str | None = None,
        timeout_seconds: float | None = None,
        max_output_chars: int | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.return_value = return_value
        self.delay = delay
        self.available = available
        self.reason = reason
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.calls = 0
        self.finished = False

    def is_available(self, context: ToolContext) -> bool:
        return self.available

    def unavailable_reason(self, context: ToolContext) -> str | None:
        return self.reason

    async def run(self, args: FakeArgs, context: ToolContext) -> ToolResult:
        self.calls += 1
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error
            if self.return_value is not _UNSET:
                return self.return_value  # type: ignore[return-value]
            return self.result or ToolResult(content=f"echo:{args.text}")
        finally:
            self.finished = True


def registry_with(tool: Tool | None = None, **kwargs) -> ToolRegistry:
    registry = ToolRegistry(**kwargs)
    registry.register(tool or FakeTool())
    return registry


def test_register_rejects_duplicate_name() -> None:
    registry = ToolRegistry()
    registry.register(FakeTool())
    with pytest.raises(ValueError):
        registry.register(FakeTool())


def test_unknown_tool_is_failed_without_running() -> None:
    registry = registry_with()
    execution = asyncio.run(registry.execute("nope", {}, CONTEXT))
    assert execution.status == "failed"
    assert execution.error_code == "unknown_tool"
    assert "fake" in execution.content


def test_disabled_tool_is_not_exposed_and_cannot_execute() -> None:
    registry = registry_with(allowed={"other"})
    assert registry.definitions(CONTEXT) == []
    execution = asyncio.run(registry.execute("fake", {"text": "x"}, CONTEXT))
    assert execution.status == "failed"
    assert execution.error_code == "tool_disabled"


def test_runtime_enable_disable() -> None:
    tool = FakeTool()
    registry = registry_with(tool)
    registry.disable("fake")
    assert registry.is_enabled("fake") is False
    registry.enable("fake")
    assert registry.is_enabled("fake") is True
    with pytest.raises(ValueError):
        registry.disable("missing")
    with pytest.raises(ValueError):
        registry.enable("missing")


def test_unavailable_tool_is_hidden_and_returns_unavailable() -> None:
    tool = FakeTool(available=False, reason="没配 key")
    registry = registry_with(tool)
    assert registry.definitions(CONTEXT) == []
    execution = asyncio.run(registry.execute("fake", {"text": "x"}, CONTEXT))
    assert execution.status == "unavailable"
    assert execution.error_code == "tool_unavailable"
    assert execution.message == "没配 key"
    assert tool.calls == 0


def test_availability_check_crash_is_treated_as_unavailable(caplog) -> None:
    class BrokenTool(FakeTool):
        def is_available(self, context: ToolContext) -> bool:
            raise RuntimeError("boom")

    registry = registry_with(BrokenTool())
    with caplog.at_level(logging.ERROR):
        assert registry.definitions(CONTEXT) == []
    assert "可用性检查失败" in caplog.text


@pytest.mark.parametrize("raw", [[], "text", None, 42])
def test_non_object_arguments_rejected(raw: object) -> None:
    registry = registry_with()
    execution = asyncio.run(registry.execute("fake", raw, CONTEXT))
    assert execution.status == "failed"
    assert execution.error_code == "invalid_arguments"


def test_missing_required_argument_rejected() -> None:
    registry = registry_with()
    execution = asyncio.run(registry.execute("fake", {}, CONTEXT))
    assert execution.error_code == "invalid_arguments"
    assert "text" in execution.content


def test_extra_argument_rejected() -> None:
    registry = registry_with()
    execution = asyncio.run(registry.execute("fake", {"text": "x", "extra": 1}, CONTEXT))
    assert execution.error_code == "invalid_arguments"
    assert "extra" in execution.content


def test_success_records_arguments_sources_and_duration() -> None:
    source = {"kind": "web", "title": "t", "url": "https://example.com"}
    tool = FakeTool(
        result=ToolResult(content="ok", message="完成", sources=(source,), data={"n": 1})
    )
    registry = registry_with(tool)
    execution = asyncio.run(registry.execute("fake", {"text": "hi"}, CONTEXT))
    assert execution.status == "success"
    assert execution.content == "ok"
    assert execution.message == "完成"
    assert execution.sources == (source,)
    assert execution.data == {"n": 1}
    assert execution.arguments == {"text": "hi"}
    assert execution.duration_ms >= 0


def test_tool_error_maps_to_failed_with_code() -> None:
    tool = FakeTool(error=ToolError("参数越界", code="file_scope_denied"))
    execution = asyncio.run(registry_with(tool).execute("fake", {"text": "x"}, CONTEXT))
    assert execution.status == "failed"
    assert execution.error_code == "file_scope_denied"
    assert "参数越界" in execution.content


def test_tool_unavailable_maps_to_unavailable() -> None:
    tool = FakeTool(error=ToolUnavailable("暂时不可用"))
    execution = asyncio.run(registry_with(tool).execute("fake", {"text": "x"}, CONTEXT))
    assert execution.status == "unavailable"
    assert execution.error_code == "tool_unavailable"


def test_unexpected_exception_is_isolated(caplog) -> None:
    tool = FakeTool(error=RuntimeError("secret-boom"))
    with caplog.at_level(logging.ERROR):
        execution = asyncio.run(
            registry_with(tool).execute("fake", {"text": "x"}, CONTEXT)
        )
    assert execution.status == "failed"
    assert execution.error_code == "internal_error"
    assert "secret-boom" not in execution.content
    assert "内部错误" in execution.content
    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_wrong_return_type_becomes_internal_error() -> None:
    tool = FakeTool(return_value="not-a-result")
    execution = asyncio.run(registry_with(tool).execute("fake", {"text": "x"}, CONTEXT))
    assert execution.status == "failed"
    assert execution.error_code == "internal_error"


def test_timeout_cancels_tool_and_runs_finally() -> None:
    tool = FakeTool(delay=0.2)
    registry = registry_with(tool, timeout_seconds=0.05)
    execution = asyncio.run(registry.execute("fake", {"text": "x"}, CONTEXT))
    assert execution.status == "timeout"
    assert execution.error_code == "timeout"
    assert tool.finished is True


def test_tool_specific_timeout_wins_when_smaller() -> None:
    tool = FakeTool(delay=0.2, timeout_seconds=0.05)
    registry = registry_with(tool, timeout_seconds=10)
    execution = asyncio.run(registry.execute("fake", {"text": "x"}, CONTEXT))
    assert execution.status == "timeout"


async def test_outer_cancellation_propagates() -> None:
    tool = FakeTool(delay=10)
    registry = registry_with(tool, timeout_seconds=30)
    task = asyncio.create_task(registry.execute("fake", {"text": "x"}, CONTEXT))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_output_truncated_to_global_cap() -> None:
    tool = FakeTool(result=ToolResult(content="x" * 100))
    registry = registry_with(tool, max_output_chars=20)
    execution = asyncio.run(registry.execute("fake", {"text": "x"}, CONTEXT))
    assert execution.truncated is True
    assert len(execution.content) <= 20
    assert execution.content.endswith(TRUNCATION_MARKER)


def test_output_truncated_to_tool_cap_when_smaller() -> None:
    tool = FakeTool(result=ToolResult(content="x" * 100), max_output_chars=10)
    registry = registry_with(tool, max_output_chars=100)
    execution = asyncio.run(registry.execute("fake", {"text": "x"}, CONTEXT))
    assert execution.truncated is True
    assert len(execution.content) <= 10


def test_content_at_cap_is_not_truncated() -> None:
    tool = FakeTool(result=ToolResult(content="x" * 20))
    registry = registry_with(tool, max_output_chars=20)
    execution = asyncio.run(registry.execute("fake", {"text": "x"}, CONTEXT))
    assert execution.truncated is False
    assert execution.content == "x" * 20


def test_allowed_none_means_all_and_empty_means_none() -> None:
    open_registry = registry_with()
    assert [tool.name for tool in open_registry.enabled(CONTEXT)] == ["fake"]

    closed_registry = registry_with(allowed=set())
    assert closed_registry.enabled(CONTEXT) == []


def test_definitions_follow_registration_order() -> None:
    first = FakeTool()
    second = FakeTool()
    second.name = "fake2"
    registry = ToolRegistry()
    registry.register(first)
    registry.register(second)
    names = [item["function"]["name"] for item in registry.definitions(CONTEXT)]
    assert names == ["fake", "fake2"]


@pytest.mark.parametrize(
    ("status", "sse", "run_status"),
    [
        ("success", "done", "success"),
        ("empty", "empty", "success"),
        ("unavailable", "unavailable", "failed"),
        ("failed", "failed", "failed"),
        ("timeout", "failed", "timeout"),
    ],
)
def test_status_mapping(status: str, sse: str, run_status: str) -> None:
    execution = ToolExecution(tool="fake", status=status)  # type: ignore[arg-type]
    assert execution.sse_status == sse
    assert execution.tool_run_status == run_status
