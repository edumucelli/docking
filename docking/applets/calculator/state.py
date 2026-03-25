"""Pure evaluation logic for Calculator applet -- no GTK dependency."""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import Any

BinaryOp = Callable[[float, float], float]
UnaryOp = Callable[[float], float]

_BINARY_OPS: dict[type[ast.operator], BinaryOp] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_UNARY_OPS: dict[type[ast.unaryop], UnaryOp] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float:
    """Recursively evaluate an AST node, allowing only basic arithmetic."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        op = _BINARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_eval_node(node.operand))
    raise ValueError("Invalid expression")


def evaluate(expression: str) -> str:
    """Safely evaluate a basic math expression.

    Supports +, -, *, /, parentheses, and decimal numbers.
    Returns the result as a string, or an error message.
    """
    expr = expression.strip()
    if not expr:
        return ""
    try:
        tree = ast.parse(expr, mode="eval")
        result = _eval_node(tree)
        if result == int(result):
            return str(int(result))
        return f"{result:.10g}"
    except ZeroDivisionError:
        return "Error: division by zero"
    except (ValueError, SyntaxError, TypeError):
        return "Error"


def prefs_payload(*, last_expression: str) -> dict[str, Any]:
    return {"last_expression": last_expression}
