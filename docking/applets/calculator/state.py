# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Pure expression evaluation for the Calculator applet.

Why this module exists

A calculator applet looks like a UI problem, but the risky part is actually
expression evaluation. The applet must accept user-entered arithmetic without
opening the door to arbitrary Python execution.

The approach here is intentionally narrow:

- parse the expression with ``ast.parse(..., mode="eval")``,
- accept only numeric constants plus a small whitelist of operators,
- recurse over the AST and reject everything else.

That gives the applet a predictable feature set: basic arithmetic, parentheses,
and unary plus/minus. It also keeps the evaluator independent from GTK, which
makes it cheap to test and easy to reuse from the popup controller.

This module also owns the tiny preference payload used to persist the last
expression/result. That persistence contract belongs with the calculator logic,
not with the GTK event handlers.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import Any

from docking.log import get_logger

log = get_logger("calculator.state")

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
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
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
    except ZeroDivisionError as exc:
        log.debug("Division by zero in calculator expression %r: %s", expr, exc)
        return "Error: division by zero"
    except (ValueError, SyntaxError, TypeError) as exc:
        log.debug("Failed to evaluate calculator expression %r: %s", expr, exc)
        return "Error"


def prefs_payload(*, last_expression: str) -> dict[str, Any]:
    return {"last_expression": last_expression}
