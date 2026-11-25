"""工具协议的形状与错误码。"""

from __future__ import annotations

from pydantic import Field

from app.tools.base import Tool, ToolArgs, ToolContext, ToolError, ToolResult


class EchoArgs(ToolArgs):
    text: str = Field(min_length=1)


class EchoTool(Tool[EchoArgs]):
    name = "echo"
    description = "回显文本"
    args_model = EchoArgs

    async def run(self, args: EchoArgs, context: ToolContext) -> ToolResult:
        return ToolResult(content=args.text)


def test_definition_is_openai_function_shape() -> None:
    definition = EchoTool().definition()
    assert definition["type"] == "function"
    function = definition["function"]
    assert function["name"] == "echo"
    assert function["description"] == "回显文本"
    assert function["parameters"]["type"] == "object"
    assert function["parameters"]["required"] == ["text"]


def test_definition_forbids_extra_arguments() -> None:
    parameters = EchoTool().definition()["function"]["parameters"]
    assert parameters["additionalProperties"] is False


def test_tool_error_default_and_custom_code() -> None:
    assert ToolError("坏了").code == "tool_error"
    custom = ToolError("越界", code="file_scope_denied")
    assert custom.code == "file_scope_denied"
    assert str(custom) == "越界"


def test_defaults_use_global_limits() -> None:
    tool = EchoTool()
    assert tool.timeout_seconds is None
    assert tool.max_output_chars is None
    assert tool.is_available(ToolContext(user_id=1)) is True
    assert tool.unavailable_reason(ToolContext(user_id=1)) is None
