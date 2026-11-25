"""安全计算器：合法表达式、数值格式与所有非法输入路径。"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from app.tools import calculator as calculator_module
from app.tools.base import ToolContext, ToolError
from app.tools.calculator import CalculatorArgs, CalculatorTool, format_number

CONTEXT = ToolContext(user_id=1)


async def evaluate(expression: str) -> str:
    result = await CalculatorTool().run(CalculatorArgs(expression=expression), CONTEXT)
    return result.content


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1+2*3", "1+2*3 = 7"),
        ("(1+2)*3", "(1+2)*3 = 9"),
        ("-4 + 1", "-4 + 1 = -3"),
        ("10//3", "10//3 = 3"),
        ("10%3", "10%3 = 1"),
        ("2**10", "2**10 = 1024"),
        ("2**-1", "2**-1 = 0.5"),
        ("abs(-4)", "abs(-4) = 4"),
        ("round(2.567, 2)", "round(2.567, 2) = 2.57"),
        ("min(3, 1, 2)", "min(3, 1, 2) = 1"),
        ("max(3, 1, 2)", "max(3, 1, 2) = 3"),
        ("pow(2, 10)", "pow(2, 10) = 1024"),
        ("sqrt(9)", "sqrt(9) = 3"),
        ("0.1+0.2", "0.1+0.2 = 0.3"),
        ("-0.0", "-0.0 = 0"),
        ("1/3", "1/3 = 0.333333333333"),
    ],
)
async def test_valid_expressions(expression: str, expected: str) -> None:
    assert await evaluate(expression) == expected


async def test_constants_pi_and_e() -> None:
    assert (await evaluate("pi")).endswith("3.14159265359")
    assert (await evaluate("e")).endswith("2.71828182846")


async def test_data_records_expression_and_type() -> None:
    result = await CalculatorTool().run(CalculatorArgs(expression="1+2"), CONTEXT)
    assert result.data == {"expression": "1+2", "value": "3", "value_type": "int"}
    assert result.message == "计算结果：3"


@pytest.mark.parametrize(
    "expression",
    [
        "x+1",
        "__import__('os')",
        "(1).__class__",
        "1 .real",
        "[1,2]",
        "'a'",
        "True + 1",
        "1 < 2",
        "1 if 2 else 3",
        "[i for i in range(3)]",
        "(lambda x: x)(1)",
        "1 & 2",
        "1 << 2",
        "round(1, 2, 3)",
        "round(1, x)",
        "sqrt(1, 2)",
        "min()",
        "abs(1, 2)",
        "unknown(1)",
        "2 @ 3",
    ],
)
async def test_rejects_unsupported_syntax(expression: str) -> None:
    with pytest.raises(ToolError) as excinfo:
        await evaluate(expression)
    assert excinfo.value.code == "invalid_expression"
    assert "Traceback" not in str(excinfo.value)


async def test_rejects_oversized_expression() -> None:
    with pytest.raises(ValidationError):
        CalculatorArgs(expression="1+" * 300 + "1")


@pytest.mark.parametrize("expression", ["2**101", "pow(2, 101)", "2**-101"])
async def test_rejects_excessive_exponent(expression: str) -> None:
    with pytest.raises(ToolError):
        await evaluate(expression)


async def test_rejects_huge_intermediate_integer() -> None:
    with pytest.raises(ToolError):
        await evaluate("(10**100)**100")


async def test_rejects_too_many_nodes() -> None:
    with pytest.raises(ToolError):
        await evaluate("+".join(["1"] * 80))


async def test_rejects_too_deep_expression() -> None:
    # 括号不会产生 AST 节点，用嵌套函数调用制造真实深度。
    with pytest.raises(ToolError):
        await evaluate("abs(" * 25 + "1" + ")" * 25)


@pytest.mark.parametrize("expression", ["1/0", "1//0", "1%0"])
async def test_rejects_division_by_zero(expression: str) -> None:
    with pytest.raises(ToolError):
        await evaluate(expression)


@pytest.mark.parametrize("expression", ["sqrt(-1)", "0**-1", "1e308*1e308"])
async def test_rejects_uncomputable_results(expression: str) -> None:
    with pytest.raises(ToolError):
        await evaluate(expression)


async def test_rejects_non_finite_float_constant() -> None:
    with pytest.raises(ToolError):
        await evaluate("1e309")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3, "3"),
        (-0.0, "0"),
        (3.0, "3"),
        (0.5, "0.5"),
        (1 / 3, "0.333333333333"),
        (1e-20, "1e-20"),
    ],
)
def test_format_number(value: int | float, expected: str) -> None:
    assert format_number(value) == expected


def test_module_never_uses_eval_or_exec() -> None:
    source = inspect.getsource(calculator_module)
    assert "eval(" not in source
    assert "exec(" not in source
