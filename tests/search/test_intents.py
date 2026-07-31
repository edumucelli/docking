"""Tests for deterministic query interpretation and utility parsing."""

from __future__ import annotations

import pytest

from docking.search.intents import (
    CANONICAL_QUERY_KEYWORDS,
    QueryIntentKind,
    complete_query_keyword,
    parse_query_intent,
)
from docking.search.recognizers.calculation import CalculationValue
from docking.search.recognizers.conversion import parse_unit_conversion
from docking.search.recognizers.temporal import TemporalValue
from docking.search.recognizers.web import get_web_engine, normalize_web_target


def test_top_level_keyword_set_is_intentionally_small() -> None:
    assert CANONICAL_QUERY_KEYWORDS == ("app", "win", "file", "web", "cmd")


@pytest.mark.parametrize(
    ("query", "providers", "search_text"),
    [
        ("app firefox", ("applications",), "firefox"),
        ("win terminal", ("windows",), "terminal"),
        ("file proposal", ("dock", "recent-files", "path"), "proposal"),
    ],
)
def test_provider_keywords_scope_the_remaining_query(
    query: str,
    providers: tuple[str, ...],
    search_text: str,
) -> None:
    intent = parse_query_intent(query)

    assert intent.kind is QueryIntentKind.SCOPED
    assert intent.provider_ids == providers
    assert intent.search_text == search_text
    assert intent.explicit


def test_cmd_keyword_routes_explicit_script_query() -> None:
    intent = parse_query_intent("cmd deploy staging")

    assert intent.kind is QueryIntentKind.SCRIPT
    assert intent.provider_ids == ("scripts",)
    assert intent.search_text == "deploy staging"


def test_tab_keyword_completion_is_deterministic() -> None:
    assert complete_query_keyword("ap") == "app "
    assert complete_query_keyword("w") is None
    assert complete_query_keyword("wi") == "win "
    assert complete_query_keyword("fi") == "file "
    assert complete_query_keyword("dd") is None
    assert complete_query_keyword("cmd") == "cmd "
    assert complete_query_keyword("c") is None
    assert complete_query_keyword("app firefox") is None


def test_calculation_conversion_url_and_web_intents() -> None:
    calculation = parse_query_intent("100 + 20")
    conversion = parse_query_intent("10 km to miles")
    url = parse_query_intent("docs.python.org/3/")
    web = parse_query_intent("web google docking linux")
    github = parse_query_intent("web gh docking")

    assert calculation.kind is QueryIntentKind.CALCULATION
    assert calculation.search_text == "=100 + 20"
    assert isinstance(calculation.recognized, CalculationValue)
    assert conversion.kind is QueryIntentKind.CONVERSION
    assert conversion.provider_ids == ("converter",)
    assert conversion.recognized is not None
    assert url.kind is QueryIntentKind.URL
    assert url.recognized == "https://docs.python.org/3/"
    assert web.kind is QueryIntentKind.WEB
    assert web.web_engine_id == "google"
    assert web.search_text == "docking linux"
    assert github.web_engine_id == "github"
    assert parse_query_intent("g docking").kind is QueryIntentKind.GLOBAL


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
