"""Deterministic interpretation of text entered in Docking Search."""

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
    raw_text: str
    search_text: str
    kind: QueryIntentKind
    provider_ids: tuple[str, ...] = ()
    recognized: _RecognizedQuery | None = None
    question_like: bool = False


def parse_query_intent(text: str) -> QueryIntent:
    """Classify a query and return provider routing plus normalized text."""
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
