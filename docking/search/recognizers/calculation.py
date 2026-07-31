"""Recognize arithmetic and evaluate a deliberately small safe AST language.

Expressions are parsed with Python's AST parser but are never compiled or
passed to ``eval``. The evaluator accepts numeric constants, a fixed table of
operators, named mathematical constants, and explicitly registered functions.
Attribute access, indexing, comprehensions, containers, lambdas, arbitrary
names, keyword arguments, and every statement form are rejected.

Length, node-count, exponent, and finite-result limits bound both work and
numeric growth. Explicit ``=`` input returns structured errors so the UI can
explain invalid syntax. Implicit input is recognized only when it contains a
known numeric value and an unmistakable operation, which keeps ordinary text
out of the calculator provider.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

_MAX_EXPRESSION_LENGTH = 256
_MAX_AST_NODES = 64
_MAX_EXPONENT = 1_000


class CalculationError(str, Enum):
    """Stable user-facing failure categories from safe evaluation."""

    INVALID = "invalid"
    DIVISION_BY_ZERO = "division-by-zero"
    DOMAIN = "domain"
    OVERFLOW = "overflow"
    TOO_COMPLEX = "too-complex"


@dataclass(frozen=True, slots=True)
class CalculationValue:
    """A parsed expression with either a formatted answer or one error."""

    expression: str
    answer: str = ""
    error: CalculationError | None = None


BinaryOperation = Callable[[float, float], float]
UnaryOperation = Callable[[float], float]
Function = Callable[..., float]

_BINARY_OPERATIONS: dict[type[ast.operator], BinaryOperation] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATIONS: dict[type[ast.unaryop], UnaryOperation] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_CONSTANTS = {"e": math.e, "pi": math.pi, "tau": math.tau}


def _rounded(value: float, digits: float = 0) -> float:
    if not digits.is_integer():
        raise ValueError("round precision must be an integer")
    return float(round(value, int(digits)))


_FUNCTIONS: dict[str, tuple[Function, frozenset[int]]] = {
    "abs": (abs, frozenset({1})),
    "acos": (math.acos, frozenset({1})),
    "asin": (math.asin, frozenset({1})),
    "atan": (math.atan, frozenset({1})),
    "ceil": (math.ceil, frozenset({1})),
    "cos": (math.cos, frozenset({1})),
    "degrees": (math.degrees, frozenset({1})),
    "exp": (math.exp, frozenset({1})),
    "floor": (math.floor, frozenset({1})),
    "ln": (math.log, frozenset({1})),
    "log": (math.log, frozenset({1, 2})),
    "log10": (math.log10, frozenset({1})),
    "radians": (math.radians, frozenset({1})),
    "round": (_rounded, frozenset({1, 2})),
    "sin": (math.sin, frozenset({1})),
    "sqrt": (math.sqrt, frozenset({1})),
    "tan": (math.tan, frozenset({1})),
}


def _finite(value: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise OverflowError("non-finite calculation result")
    return numeric


def _evaluate_node(node: ast.AST) -> float:
    # This explicit dispatcher is the security boundary. New AST node types
    # must remain rejected unless their complete behavior is understood and
    # covered by the same complexity and finite-number limits.
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body)
    if (
        isinstance(node, ast.Constant)
        and not isinstance(node.value, bool)
        and isinstance(node.value, int | float)
    ):
        return _finite(node.value)
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.BinOp):
        operation = _BINARY_OPERATIONS.get(type(node.op))
        if operation is None:
            raise ValueError("unsupported operator")
        left = _evaluate_node(node.left)
        right = _evaluate_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise OverflowError("exponent is too large")
        return _finite(operation(left, right))
    if isinstance(node, ast.UnaryOp):
        operation = _UNARY_OPERATIONS.get(type(node.op))
        if operation is None:
            raise ValueError("unsupported unary operator")
        return _finite(operation(_evaluate_node(node.operand)))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and not node.keywords
    ):
        function_spec = _FUNCTIONS.get(node.func.id)
        if function_spec is None:
            raise ValueError("unsupported function")
        function, accepted_counts = function_spec
        if len(node.args) not in accepted_counts:
            raise ValueError("invalid function argument count")
        arguments = tuple(_evaluate_node(argument) for argument in node.args)
        return _finite(function(*arguments))
    raise ValueError("invalid expression")


def _has_implicit_signal(tree: ast.Expression) -> bool:
    has_operation = any(
        isinstance(node, ast.BinOp)
        or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS
        )
        for node in ast.walk(tree)
    )
    has_known_value = any(
        (
            isinstance(node, ast.Constant)
            and not isinstance(node.value, bool)
            and isinstance(node.value, int | float)
        )
        or (isinstance(node, ast.Name) and node.id in _CONSTANTS)
        or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS
        )
        for node in ast.walk(tree)
    )
    return has_operation and has_known_value


def _is_supported_node(node: ast.AST) -> bool:
    if isinstance(node, ast.Expression):
        return _is_supported_node(node.body)
    if isinstance(node, ast.Constant):
        return not isinstance(node.value, bool) and isinstance(node.value, int | float)
    if isinstance(node, ast.Name):
        return node.id in _CONSTANTS
    if isinstance(node, ast.BinOp):
        return (
            type(node.op) in _BINARY_OPERATIONS
            and _is_supported_node(node.left)
            and _is_supported_node(node.right)
        )
    if isinstance(node, ast.UnaryOp):
        return type(node.op) in _UNARY_OPERATIONS and _is_supported_node(node.operand)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function_spec = _FUNCTIONS.get(node.func.id)
        return (
            function_spec is not None
            and not node.keywords
            and len(node.args) in function_spec[1]
            and all(_is_supported_node(argument) for argument in node.args)
        )
    return False


def _format_answer(value: float) -> str:
    if value == 0:
        value = 0.0
    if value.is_integer():
        return str(int(value))
    return f"{value:.12g}"


def recognize_calculation(text: str) -> CalculationValue | None:
    """Recognize and safely evaluate explicit or unambiguous arithmetic."""
    stripped = text.strip()
    explicit = stripped.startswith("=")
    expression = stripped.removeprefix("=").strip() if explicit else stripped
    if not expression:
        return None
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        return (
            CalculationValue(expression, error=CalculationError.TOO_COMPLEX)
            if explicit
            else None
        )
    normalized_expression = expression.replace("^", "**")
    try:
        tree = ast.parse(normalized_expression, mode="eval")
    except SyntaxError:
        return (
            CalculationValue(expression, error=CalculationError.INVALID)
            if explicit
            else None
        )
    if not isinstance(tree, ast.Expression):
        return None
    if not explicit and (
        not _has_implicit_signal(tree) or not _is_supported_node(tree)
    ):
        return None
    if sum(1 for _node in ast.walk(tree)) > _MAX_AST_NODES:
        return CalculationValue(expression, error=CalculationError.TOO_COMPLEX)
    try:
        answer = _format_answer(_evaluate_node(tree))
    except ZeroDivisionError:
        error = CalculationError.DIVISION_BY_ZERO
    except OverflowError:
        error = CalculationError.OVERFLOW
    except ValueError:
        error = CalculationError.DOMAIN
    except TypeError:
        error = CalculationError.INVALID
    else:
        return CalculationValue(expression, answer=answer)
    return CalculationValue(expression, error=error)


__all__ = [
    "CalculationError",
    "CalculationValue",
    "recognize_calculation",
]
