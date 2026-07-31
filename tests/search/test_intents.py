"""Tests for deterministic query interpretation and utility parsing."""

from __future__ import annotations

import pytest

from docking.search.intents import (
    QueryIntentKind,
    parse_query_intent,
)
from docking.search.recognizers.calculation import CalculationValue
from docking.search.recognizers.conversion import parse_unit_conversion
from docking.search.recognizers.temporal import TemporalValue
from docking.search.recognizers.web import get_web_engine, normalize_web_target


@pytest.mark.parametrize(
    "query",
    [
        "app firefox",
        "win terminal",
        "file proposal",
        "web gh docking",
        "cmd deploy",
    ],
)
def test_provider_words_are_ordinary_query_text(query: str) -> None:
    intent = parse_query_intent(query)

    assert intent.kind is QueryIntentKind.GLOBAL
    assert intent.provider_ids == ()
    assert intent.search_text == query


def test_question_form_only_boosts_web_fallback() -> None:
    question = parse_query_intent("What is a Linux dockbar?")

    assert question.question_like
    assert question.kind is QueryIntentKind.GLOBAL
    assert parse_query_intent("Firefox").kind is QueryIntentKind.GLOBAL
    assert not parse_query_intent("Firefox").question_like


def test_calculation_conversion_and_url_intents() -> None:
    calculation = parse_query_intent("100 + 20")
    conversion = parse_query_intent("10 km to miles")
    url = parse_query_intent("docs.python.org/3/")

    assert calculation.kind is QueryIntentKind.CALCULATION
    assert calculation.search_text == "=100 + 20"
    assert isinstance(calculation.recognized, CalculationValue)
    assert conversion.kind is QueryIntentKind.CONVERSION
    assert conversion.provider_ids == ("converter",)
    assert conversion.recognized is not None
    assert url.kind is QueryIntentKind.URL
    assert url.recognized == "https://docs.python.org/3/"


def test_scientific_calculations_are_recognized_without_hijacking_words() -> None:
    power = parse_query_intent("2^8")
    function = parse_query_intent("sqrt(9)")

    assert power.kind is QueryIntentKind.CALCULATION
    assert function.kind is QueryIntentKind.CALCULATION
    assert parse_query_intent("pi").kind is QueryIntentKind.GLOBAL
    assert parse_query_intent("rock + roll").kind is QueryIntentKind.GLOBAL
    assert parse_query_intent("2 + apples").kind is QueryIntentKind.GLOBAL


def test_dates_timezones_and_normal_words_are_distinguished() -> None:
    date = parse_query_intent("2026-07-28")
    assert date.kind is QueryIntentKind.TEMPORAL
    assert isinstance(date.recognized, TemporalValue)
    assert parse_query_intent("time in Tokyo").kind is QueryIntentKind.TEMPORAL
    assert parse_query_intent("visual studio code").kind is QueryIntentKind.GLOBAL


def test_static_unit_conversion_handles_aliases_and_temperature() -> None:
    distance = parse_unit_conversion("10 km to miles")
    temperature = parse_unit_conversion("32 F to celsius")

    assert distance is not None
    assert distance.target.symbol == "mi"
    assert distance.result == pytest.approx(6.21371, rel=1e-5)
    assert temperature is not None
    assert temperature.result == pytest.approx(0)


def test_currency_conversion_is_routed_to_converter() -> None:
    intent = parse_query_intent("10 USD to EUR")

    assert intent.kind is QueryIntentKind.CONVERSION
    assert intent.provider_ids == ("converter",)


def test_web_targets_and_templates_are_conservative() -> None:
    assert normalize_web_target("user@example.com") == "mailto:user@example.com"
    assert normalize_web_target("mailto:user@example.com") == (
        "mailto:user@example.com"
    )
    assert normalize_web_target("not a url") is None
    assert get_web_engine("duckduckgo").url_for("docking linux") == (
        "https://duckduckgo.com/?q=docking+linux"
    )
