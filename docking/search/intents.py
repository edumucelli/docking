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
from docking.search.recognizers.web import (
    DEFAULT_WEB_ENGINE,
    get_web_engine_by_keyword,
    normalize_web_target,
)


class QueryIntentKind(str, Enum):
    GLOBAL = "global"
    SCOPED = "scoped"
    CALCULATION = "calculation"
    CONVERSION = "conversion"
    URL = "url"
    WEB = "web"
    PATH = "path"
    TEMPORAL = "temporal"
    SCRIPT = "script"


_RecognizedQuery: TypeAlias = (
    CalculationValue | CurrencyConversionRequest | TemporalValue | UnitConversion | str
)


@dataclass(frozen=True, slots=True)
class QueryIntent:
    raw_text: str
    search_text: str
    kind: QueryIntentKind
    provider_ids: tuple[str, ...] = ()
    explicit: bool = False
    web_engine_id: str | None = None
    recognized: _RecognizedQuery | None = None


CANONICAL_QUERY_KEYWORDS = ("app", "win", "file", "web", "cmd")
_PROVIDER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "app": ("applications",),
    "win": ("windows",),
    "file": ("dock", "recent-files", "path"),
}
_SCRIPT_KEYWORD = "cmd"
_WEB_KEYWORD = "web"
_COMPLETION_KEYWORDS = CANONICAL_QUERY_KEYWORDS


def _split_keyword(text: str) -> tuple[str, str]:
    parts = text.split(maxsplit=1)
    return parts[0].casefold(), parts[1].strip() if len(parts) == 2 else ""


def complete_query_keyword(text: str) -> str | None:
    """Complete an unambiguous provider/utility keyword for Tab."""
    stripped = text.strip().casefold()
    if not stripped or any(character.isspace() for character in stripped):
        return None
    if len(stripped) < 2 and stripped not in CANONICAL_QUERY_KEYWORDS:
        return None
    matches = [
        keyword for keyword in _COMPLETION_KEYWORDS if keyword.startswith(stripped)
    ]
    if not matches:
        return None
    exact = next((keyword for keyword in matches if keyword == stripped), None)
    if exact is not None:
        return f"{exact} "
    return f"{matches[0]} " if len(matches) == 1 else None


def parse_query_intent(
    text: str,
    *,
    default_web_engine: str = DEFAULT_WEB_ENGINE,
) -> QueryIntent:
    """Classify a query and return provider routing plus normalized text."""
    raw_text = text
    stripped = text.strip()
    if not stripped:
        return QueryIntent(raw_text, "", QueryIntentKind.GLOBAL)

    keyword, remainder = _split_keyword(stripped)
    if keyword in _PROVIDER_KEYWORDS:
        return QueryIntent(
            raw_text,
            remainder,
            QueryIntentKind.SCOPED,
            provider_ids=_PROVIDER_KEYWORDS[keyword],
            explicit=True,
        )
    if keyword == _SCRIPT_KEYWORD:
        return QueryIntent(
            raw_text,
            remainder,
            QueryIntentKind.SCRIPT,
            provider_ids=("scripts",),
            explicit=True,
        )
    if keyword == _WEB_KEYWORD:
        engine_keyword, engine_query = (
            _split_keyword(remainder) if remainder else ("", "")
        )
        web_engine = get_web_engine_by_keyword(engine_keyword)
        return QueryIntent(
            raw_text,
            engine_query if web_engine is not None else remainder,
            QueryIntentKind.WEB,
            provider_ids=("web",),
            explicit=True,
            web_engine_id=(
                web_engine.id if web_engine is not None else default_web_engine
            ),
        )

    if stripped.startswith("="):
        calculation = recognize_calculation(stripped)
        return QueryIntent(
            raw_text,
            stripped,
            QueryIntentKind.CALCULATION,
            provider_ids=("calculator",),
            explicit=True,
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
    return QueryIntent(raw_text, stripped, QueryIntentKind.GLOBAL)


__all__ = [
    "CANONICAL_QUERY_KEYWORDS",
    "QueryIntent",
    "QueryIntentKind",
    "complete_query_keyword",
    "parse_query_intent",
]
