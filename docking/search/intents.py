"""Deterministic interpretation of text entered in Docking Search."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from docking.applets.calculator.state import evaluate
from docking.search.conversion import (
    parse_currency_conversion,
    parse_unit_conversion,
)
from docking.search.temporal import parse_temporal_query
from docking.search.web import (
    DEFAULT_WEB_ENGINE,
    WEB_ENGINE_BY_KEYWORD,
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


@dataclass(frozen=True, slots=True)
class QueryIntent:
    raw_text: str
    search_text: str
    kind: QueryIntentKind
    provider_ids: tuple[str, ...] = ()
    explicit: bool = False
    web_engine_id: str | None = None


CANONICAL_QUERY_KEYWORDS = ("app", "win", "file", "web", "cmd")
_PROVIDER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "app": ("applications",),
    "win": ("windows",),
    "file": ("dock", "recent-files", "path"),
}
_SCRIPT_KEYWORD = "cmd"
_WEB_KEYWORD = "web"
_COMPLETION_KEYWORDS = CANONICAL_QUERY_KEYWORDS
_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")
_CALCULATION_CHARACTERS_RE = re.compile(r"^[\d\s()+\-*/.]+$")


def _split_keyword(text: str) -> tuple[str, str]:
    parts = text.split(maxsplit=1)
    return parts[0].casefold(), parts[1].strip() if len(parts) == 2 else ""


def _looks_like_calculation(text: str) -> bool:
    expression = text.strip()
    if not expression or _DATE_RE.fullmatch(expression):
        return False
    if not _CALCULATION_CHARACTERS_RE.fullmatch(expression):
        return False
    operator_positions = [
        index
        for index, character in enumerate(expression)
        if character in "+-*/" and index > 0
    ]
    if not operator_positions or not any(
        character.isdigit() for character in expression
    ):
        return False
    answer = evaluate(expression)
    return bool(answer) and not answer.startswith("Error")


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
        web_engine = WEB_ENGINE_BY_KEYWORD.get(engine_keyword)
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
        return QueryIntent(
            raw_text,
            stripped,
            QueryIntentKind.CALCULATION,
            provider_ids=("calculator",),
            explicit=True,
        )
    if stripped.startswith(("/", "~/", "./", "../", "file://")):
        return QueryIntent(
            raw_text,
            stripped,
            QueryIntentKind.PATH,
            provider_ids=("path",),
        )
    if parse_temporal_query(stripped) is not None:
        return QueryIntent(
            raw_text,
            stripped,
            QueryIntentKind.TEMPORAL,
            provider_ids=("datetime",),
        )
    if (
        parse_unit_conversion(stripped) is not None
        or parse_currency_conversion(stripped) is not None
    ):
        return QueryIntent(
            raw_text,
            stripped,
            QueryIntentKind.CONVERSION,
            provider_ids=("converter",),
        )
    if _looks_like_calculation(stripped):
        return QueryIntent(
            raw_text,
            f"={stripped}",
            QueryIntentKind.CALCULATION,
            provider_ids=("calculator",),
        )
    if normalize_web_target(stripped) is not None:
        return QueryIntent(
            raw_text,
            stripped,
            QueryIntentKind.URL,
            provider_ids=("web",),
        )
    return QueryIntent(raw_text, stripped, QueryIntentKind.GLOBAL)


__all__ = [
    "CANONICAL_QUERY_KEYWORDS",
    "QueryIntent",
    "QueryIntentKind",
    "complete_query_keyword",
    "parse_query_intent",
]
