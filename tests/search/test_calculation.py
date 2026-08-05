"""Tests for safe inline calculation recognition."""

from __future__ import annotations

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
