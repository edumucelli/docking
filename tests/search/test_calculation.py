"""Tests for safe inline calculation recognition."""

from __future__ import annotations

import pytest

from docking.search.recognizers.calculation import (
    CalculationError,
    recognize_calculation,
)


def test_practical_arithmetic_and_scientific_syntax() -> None:
    power = recognize_calculation("2^8")
    scientific = recognize_calculation("1e3 + 25")
    modulo = recognize_calculation("17 % 5")
    function = recognize_calculation("sqrt(9) + cos(0)")

    assert power is not None and power.answer == "256"
    assert scientific is not None and scientific.answer == "1025"
    assert modulo is not None and modulo.answer == "2"
    assert function is not None and function.answer == "4"


def test_constants_require_explicit_input_without_an_operation() -> None:
    assert recognize_calculation("pi") is None
    value = recognize_calculation("= pi")

    assert value is not None
    assert value.answer == "3.14159265359"


@pytest.mark.parametrize(
    ("expression", "answer"),
    [
        ("round(1.6)", "2"),
        ("= round(-1.6)", "-2"),
        ("round(2.5)", "2"),
        ("round(1.56, 1)", "1.6"),
        ("round(125, -1)", "120"),
    ],
)
def test_round_with_optional_precision(expression: str, answer: str) -> None:
    value = recognize_calculation(expression)

    assert value is not None
    assert value.error is None
    assert value.answer == answer


def test_round_rejects_fractional_precision() -> None:
    value = recognize_calculation("= round(1.56, 1.5)")

    assert value is not None
    assert value.error is CalculationError.DOMAIN


def test_invalid_and_dangerous_expressions_are_contained() -> None:
    division = recognize_calculation("= 1 / 0")
    huge = recognize_calculation("= 2^1001")

    assert division is not None
    assert division.error is CalculationError.DIVISION_BY_ZERO
    assert huge is not None
    assert huge.error is CalculationError.OVERFLOW
    assert recognize_calculation("__import__('os').getcwd()") is None
    assert recognize_calculation("ordinary words") is None
    assert recognize_calculation("rock + roll") is None
    assert recognize_calculation("2 + apples") is None


def test_division_by_zero_covers_both_operators() -> None:
    for expression in ("= 1 / 0", "= 5 % 0", "= 7 % (2 - 2)"):
        value = recognize_calculation(expression)

        assert value is not None, expression
        assert value.error is CalculationError.DIVISION_BY_ZERO, expression


def test_overflow_from_exponent_limit_and_non_finite_results() -> None:
    """Overflow is caught both before the power is taken and after it lands."""
    too_large_exponent = recognize_calculation("= 2^1001")
    negative_exponent = recognize_calculation("= 2^-1001")
    non_finite_product = recognize_calculation("= 1e308 * 10")
    deep_power = recognize_calculation("= 10^400")
    transcendental = recognize_calculation("= exp(1000)")

    for value in (
        too_large_exponent,
        negative_exponent,
        non_finite_product,
        deep_power,
        transcendental,
    ):
        assert value is not None
        assert value.error is CalculationError.OVERFLOW

    # The exponent limit is a ceiling, not a blanket ban on large powers.
    within_limit = recognize_calculation("= 2^1000")
    assert within_limit is not None and within_limit.error is None


def test_domain_errors_cover_math_and_rejected_operators() -> None:
    """All ValueError paths collapse to one user-facing category."""
    for expression in (
        "= sqrt(-1)",
        "= log(-1)",
        "= asin(2)",
        "= round(1.5, 0.5)",  # non-integer precision
        "= 5 // 2",  # unsupported operator
        "= ~5",  # unsupported unary operator
        "= foo(1)",  # unsupported function
        "= sin(1, 2)",  # wrong function arity
        "= x",  # unknown name
        "= 'text'",  # non-numeric constant
    ):
        value = recognize_calculation(expression)

        assert value is not None, expression
        assert value.error is CalculationError.DOMAIN, expression


def test_dangerous_constructs_never_execute() -> None:
    """The AST dispatcher rejects every non-arithmetic node form.

    Explicit ``=`` input surfaces the rejection as an error rather than
    returning silently, so no attribute, subscript, call into an unknown
    name, comprehension, or literal container can reach the interpreter.
    """
    for expression in (
        "= __import__('os').system('true')",
        "= (1).__class__",
        "= [1, 2][0]",
        "= {1: 2}",
        "= (1, 2)",
        "= [n for n in (1, 2)]",
        "= lambda: 1",
        "= 1 if 2 else 3",
        "= 1 < 2",
        "= 1 and 2",
        "= 1 | 2",
        "= f'{1}'",
        "= str(1)",
    ):
        value = recognize_calculation(expression)

        assert value is not None, expression
        assert value.error is not None, expression
        assert value.answer == "", expression


def test_implicit_input_requires_an_operation_and_a_known_value() -> None:
    assert recognize_calculation("pi") is None
    assert recognize_calculation("e") is None
    assert recognize_calculation("tau") is None
    assert recognize_calculation("5") is None
    assert recognize_calculation("grocery") is None

    # A bare function call supplies both halves of the signal.
    assert recognize_calculation("sin(0)").answer == "0"
    assert recognize_calculation("2 + 2").answer == "4"
    assert recognize_calculation("2*3").answer == "6"


def test_constants_and_functions_evaluate_explicitly() -> None:
    assert recognize_calculation("= e").answer == "2.71828182846"
    assert recognize_calculation("= tau").answer == "6.28318530718"
    assert recognize_calculation("= log(8, 2)").answer == "3"
    assert recognize_calculation("= log10(1000)").answer == "3"
    assert recognize_calculation("= degrees(pi)").answer == "180"
    assert recognize_calculation("= abs(-3) + floor(2.7)").answer == "5"


def test_answer_formatting_trailing_rules() -> None:
    negative_zero = recognize_calculation("= -0.0")
    subtracted_to_zero = recognize_calculation("= 0 - 0")

    assert negative_zero is not None and negative_zero.answer == "0"
    assert subtracted_to_zero is not None and subtracted_to_zero.answer == "0"
    # Integers print without a decimal point, fractions use 12 digits.
    assert recognize_calculation("= 100 / 8").answer == "12.5"
    assert recognize_calculation("= 2^64").answer == "18446744073709551616"
    assert recognize_calculation("= sqrt(2)").answer == "1.41421356237"
    assert recognize_calculation("= 1e-15").answer == "1e-15"


def test_complexity_limits_split_implicit_and_explicit_input() -> None:
    by_length = "1" + " + 1" * 100
    by_node_count = "=" + "+".join(["1"] * 30)

    assert len(by_length) > 256
    explicit_length = recognize_calculation("=" + by_length)
    node_heavy = recognize_calculation(by_node_count)

    assert explicit_length is not None
    assert explicit_length.error is CalculationError.TOO_COMPLEX
    assert node_heavy is not None
    assert node_heavy.error is CalculationError.TOO_COMPLEX
    # Implicit input is dropped rather than reported, so plain prose that
    # merely looks arithmetic never reaches the result list.
    assert recognize_calculation(by_length) is None


def test_implicit_input_accepts_unary_signs_but_not_comparisons() -> None:
    """A unary sign is arithmetic; a comparison is prose-like and dropped."""
    assert recognize_calculation("-2 + 3").answer == "1"
    assert recognize_calculation("2 * -3").answer == "-6"
    assert recognize_calculation("sin(0) + -1").answer == "-1"

    # These contain a recognizable sum, but the comparison at the root is
    # not arithmetic, so the implicit path must decline to answer.
    assert recognize_calculation("2 + 3 == 5") is None
    assert recognize_calculation("2 + 3 > 1") is None


def test_empty_and_separator_only_queries_are_ignored() -> None:
    assert recognize_calculation("") is None
    assert recognize_calculation("   ") is None
    assert recognize_calculation("=") is None
    assert recognize_calculation("=   ") is None
