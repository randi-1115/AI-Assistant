"""安全计算器：AST 白名单求值，绝不使用 eval/exec。

模型可以自由生成表达式，所以这里把"能算什么"写得非常窄：
- 只允许数字、括号、四则/整除/取模/幂、一元正负号；
- 只允许 abs/round/min/max/sqrt/pow 五个函数和 pi/e 两个常量；
- 属性访问、下标、lambda、推导式、比较、字符串、容器一律拒绝；
- 节点数、深度、指数、中间结果大小都有上限，避免 `2**1000000` 这类输入
  让进程卡死或吃光内存。
"""

from __future__ import annotations

import ast
import math
from typing import Any

from pydantic import Field

from app.tools.base import Tool, ToolArgs, ToolContext, ToolError, ToolResult

MAX_EXPRESSION_CHARS = 500
MAX_NODES = 100
MAX_DEPTH = 20
MAX_EXPONENT = 100
MAX_INT_BITS = 4096
MAX_FLOAT_ABS = 1e100

_ALLOWED_BIN_OPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
)
_ALLOWED_UNARY_OPS = (ast.UAdd, ast.USub)
_CONSTANTS = {"pi": math.pi, "e": math.e}
_FUNCTIONS = frozenset({"abs", "round", "min", "max", "sqrt", "pow"})


class CalculatorArgs(ToolArgs):
    expression: str = Field(
        min_length=1,
        max_length=MAX_EXPRESSION_CHARS,
        description="数学表达式，例如 (1+2)*3、sqrt(2)、2**10。",
    )


def _invalid(message: str) -> ToolError:
    return ToolError(message, code="invalid_expression")


def _check_constant(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid("只支持数字常量")
    return _guard(value)


def _guard(value: Any) -> int | float:
    """限制中间结果大小，避免整数爆炸和浮点溢出。"""
    if isinstance(value, bool):
        raise _invalid("不支持布尔值")
    if isinstance(value, int):
        if value.bit_length() > MAX_INT_BITS:
            raise _invalid("结果过大")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > MAX_FLOAT_ABS:
            raise _invalid("结果超出可计算范围")
        return value
    raise _invalid("结果不是数字")


def _depth(node: ast.AST) -> int:
    children = list(ast.iter_child_nodes(node))
    if not children:
        return 1
    return 1 + max(_depth(child) for child in children)


def _validate(node: ast.AST) -> None:
    """静态检查整棵树；先全部校验，再执行，避免"算到一半才发现非法节点"。

    用显式递归而不是 `ast.walk` 统一判断节点类型：运算符对象（ast.Add 等）
    和 `Call.func` 里的函数名有各自的合法位置，不能当普通节点处理。
    """
    if len(list(ast.walk(node))) > MAX_NODES:
        raise _invalid("表达式过于复杂")
    if _depth(node) > MAX_DEPTH:
        raise _invalid("表达式嵌套过深")
    _check_node(node)


def _check_node(node: ast.AST) -> None:
    if isinstance(node, ast.Expression):
        _check_node(node.body)
        return
    if isinstance(node, ast.Constant):
        _check_constant(node.value)
        return
    if isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):
            raise _invalid("不支持的名称用法")
        if node.id not in _CONSTANTS:
            raise _invalid(f"未知名字：{node.id}")
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARY_OPS):
            raise _invalid(f"不支持的一元运算符：{type(node.op).__name__}")
        _check_node(node.operand)
        return
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BIN_OPS):
            raise _invalid(f"不支持的运算符：{type(node.op).__name__}")
        _check_node(node.left)
        _check_node(node.right)
        return
    if isinstance(node, ast.Call):
        _validate_call(node)
        return
    raise _invalid(f"不支持的语法：{type(node).__name__}")


def _validate_call(node: ast.Call) -> None:
    if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
        raise _invalid("只支持 abs/round/min/max/sqrt/pow 这些函数")
    if node.keywords:
        raise _invalid("函数调用不支持关键字参数")
    if any(isinstance(arg, ast.Starred) for arg in node.args):
        raise _invalid("函数调用不支持展开参数")

    name = node.func.id
    count = len(node.args)
    if name in {"abs", "sqrt"} and count != 1:
        raise _invalid(f"{name} 需要 1 个参数")
    if name == "pow" and count != 2:
        raise _invalid("pow 需要 2 个参数")
    if name == "round":
        if count not in (1, 2):
            raise _invalid("round 需要 1 或 2 个参数")
        if count == 2:
            precision = node.args[1]
            if not (
                isinstance(precision, ast.Constant)
                and isinstance(precision.value, int)
                and not isinstance(precision.value, bool)
                and -1000 <= precision.value <= 1000
            ):
                raise _invalid("round 的第二个参数必须是整数常量")
    if name in {"min", "max"} and not 1 <= count <= 8:
        raise _invalid(f"{name} 需要 1–8 个参数")

    for arg in node.args:
        _check_node(arg)


def _evaluate(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        return _guard(node.value)
    if isinstance(node, ast.Name):
        return _CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp):
        value = _evaluate(node.operand)
        return _guard(value if isinstance(node.op, ast.UAdd) else -value)
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        return _apply_binop(node.op, left, right)
    if isinstance(node, ast.Call):
        return _apply_call(node)
    # _validate 已经挡掉所有未知节点，这里只是防御性兜底。
    raise _invalid("不支持的表达式")


def _apply_binop(op: ast.operator, left: int | float, right: int | float) -> int | float:
    if isinstance(op, ast.Add):
        return _guard(left + right)
    if isinstance(op, ast.Sub):
        return _guard(left - right)
    if isinstance(op, ast.Mult):
        return _guard(left * right)
    if isinstance(op, ast.Div):
        if right == 0:
            raise _invalid("除数不能为 0")
        return _guard(left / right)
    if isinstance(op, ast.FloorDiv):
        if right == 0:
            raise _invalid("除数不能为 0")
        return _guard(left // right)
    if isinstance(op, ast.Mod):
        if right == 0:
            raise _invalid("除数不能为 0")
        return _guard(left % right)
    if isinstance(op, ast.Pow):
        return _power(left, right)
    raise _invalid("不支持的运算符")


def _power(base: int | float, exponent: int | float) -> int | float:
    if abs(exponent) > MAX_EXPONENT:
        raise _invalid(f"指数绝对值不能超过 {MAX_EXPONENT}")
    try:
        return _guard(base**exponent)
    except (OverflowError, ZeroDivisionError, ValueError) as exc:
        raise _invalid("幂运算无法计算") from exc


def _apply_call(node: ast.Call) -> int | float:
    name = node.func.id
    values = [_evaluate(arg) for arg in node.args]
    try:
        if name == "abs":
            return _guard(abs(values[0]))
        if name == "round":
            if len(values) == 2:
                return _guard(round(values[0], values[1]))
            return _guard(round(values[0]))
        if name == "min":
            return _guard(min(values))
        if name == "max":
            return _guard(max(values))
        if name == "sqrt":
            if values[0] < 0:
                raise _invalid("sqrt 的参数不能为负数")
            return _guard(math.sqrt(values[0]))
        if name == "pow":
            return _power(values[0], values[1])
    except (OverflowError, ZeroDivisionError, ValueError, TypeError) as exc:
        raise _invalid("函数运算无法计算") from exc
    raise _invalid("不支持的函数")


def format_number(value: int | float) -> str:
    """稳定输出：整数不带小数点，浮点去掉尾零和浮点噪声。"""
    if isinstance(value, int):
        return str(value)
    if value == 0:
        return "0"  # 同时处理 -0.0
    if value.is_integer() and abs(value) < 1e16:
        return str(int(value))
    return format(value, ".12g")


class CalculatorTool(Tool[CalculatorArgs]):
    name = "calculator"
    description = (
        "计算数学表达式。支持 + - * / // % **、括号、abs/round/min/max/sqrt/pow "
        "以及 pi/e 常量。需要精确算术时优先使用这个工具，不要心算。"
    )
    args_model = CalculatorArgs
    timeout_seconds = 2.0
    max_output_chars = 1000

    async def run(self, args: CalculatorArgs, context: ToolContext) -> ToolResult:
        expression = args.expression.strip()
        try:
            tree = ast.parse(expression, mode="eval")
            _validate(tree)
            value = _evaluate(tree)
        except ToolError:
            raise
        except (SyntaxError, RecursionError, MemoryError) as exc:
            raise _invalid("表达式无法解析") from exc
        except (OverflowError, ZeroDivisionError, ValueError, TypeError) as exc:
            raise _invalid("表达式无法计算") from exc

        formatted = format_number(value)
        value_type = "int" if isinstance(value, int) else "float"
        message = f"计算结果：{formatted}"
        return ToolResult(
            content=f"{expression} = {formatted}",
            message=message,
            data={
                "expression": expression,
                "value": formatted,
                "value_type": value_type,
            },
        )
