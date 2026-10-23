"""Recognize structured queries and choose the providers that should run.

Intent parsing is deliberately keyword-free. Users type the value they want,
and conservative recognizers decide whether it is a calculation, path, date or
time expression, conversion, or direct web target. Recognized utilities route
exclusively to their matching provider so unrelated fuzzy results cannot crowd
out an exact answer. Ordinary text keeps its literal spelling and searches all
default local providers, with web handled later as a fallback.

Recognition order is product policy. Explicit calculations and path syntax are
checked before more general recognizers, while URL recognition comes after
math, conversion, and temporal parsing. A recognized value is carried in the
intent so the provider can reuse it instead of parsing the text a second time.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from docking.search.recognizers.calculation import (
    CalculationValue,
    recognize_calculation,
)
from docking.search.recognizers.conversion import (
    CurrencyConversionRequest,
    UnitConversion,
    parse_currency_conversion,
    parse_unit_conversion,
)
from docking.search.recognizers.temporal import TemporalValue, parse_temporal_query
from docking.search.recognizers.web import is_likely_web_question, normalize_web_target


class QueryIntentKind(str, Enum):
    """Stable categories used for routing and provider context."""

    GLOBAL = "global"
    CALCULATION = "calculation"
    CONVERSION = "conversion"
    URL = "url"
    PATH = "path"
    TEMPORAL = "temporal"


_RecognizedQuery: TypeAlias = (
    CalculationValue | CurrencyConversionRequest | TemporalValue | UnitConversion | str
)


@dataclass(frozen=True, slots=True)
class QueryIntent:
    """One deterministic interpretation of the user's current entry text.

    ``raw_text`` preserves what the entry contained. ``search_text`` is the
    value providers receive and may include a normalization required by a
    utility provider. ``provider_ids`` is empty for ordinary global search and
    exclusive for a recognized intent. ``recognized`` is immutable parsed data
    shared with the selected provider.
    """

    raw_text: str
    search_text: str
    kind: QueryIntentKind
    provider_ids: tuple[str, ...] = ()
    recognized: _RecognizedQuery | None = None
    question_like: bool = False


def parse_query_intent(text: str) -> QueryIntent:
    """Classify text and return its routing plus reusable recognized value."""
    raw_text = text
    stripped = text.strip()
    if not stripped:
        return QueryIntent(raw_text, "", QueryIntentKind.GLOBAL)

    if stripped.startswith("="):
        calculation = recognize_calculation(stripped)
        return QueryIntent(
            raw_text,
            stripped,
            QueryIntentKind.CALCULATION,
            provider_ids=("calculator",),
            recognized=calculation,
        )
    if stripped.startswith(("/", "~/", "./", "../", "file://")):
        return QueryIntent(
            raw_text,
            stripped,
            QueryIntentKind.PATH,
            provider_ids=("path",),
        )
    temporal = parse_temporal_query(stripped)
    if temporal is not None:
        return QueryIntent(
            raw_text,
            stripped,
            QueryIntentKind.TEMPORAL,
            provider_ids=("datetime",),
            recognized=temporal,
        )
    conversion: UnitConversion | CurrencyConversionRequest | None = (
        parse_unit_conversion(stripped)
    )
    if conversion is None:
        conversion = parse_currency_conversion(stripped)
    if conversion is not None:
        return QueryIntent(
            raw_text,
            stripped,
            QueryIntentKind.CONVERSION,
            provider_ids=("converter",),
            recognized=conversion,
        )
    calculation = recognize_calculation(stripped)
    if calculation is not None:
        return QueryIntent(
            raw_text,
            f"={stripped}",
            QueryIntentKind.CALCULATION,
            provider_ids=("calculator",),
            recognized=calculation,
        )
    web_target = normalize_web_target(stripped)
    if web_target is not None:
        return QueryIntent(
            raw_text,
            stripped,
            QueryIntentKind.URL,
            provider_ids=("web",),
            recognized=web_target,
        )
    return QueryIntent(
        raw_text,
        stripped,
        QueryIntentKind.GLOBAL,
        question_like=is_likely_web_question(stripped),
    )


__all__ = [
    "QueryIntent",
    "QueryIntentKind",
    "parse_query_intent",
]
