"""Deterministic text matching and scoring for global search providers."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

from docking.search.types import SearchQuery

MATCH_QUALITY_LIMIT = 49.0
SOURCE_BOOST_LIMIT = 20.0
STATE_BOOST_LIMIT = 20.0


class MatchTier(IntEnum):
    """Ordered match tiers; larger values are stronger matches."""

    FUZZY = 1
    SUBSTRING = 2
    TOKEN_START = 3
    PREFIX = 4
    EXACT = 5


TIER_BASE_SCORE: dict[MatchTier, float] = {
    MatchTier.FUZZY: 100.0,
    MatchTier.SUBSTRING: 300.0,
    MatchTier.TOKEN_START: 500.0,
    MatchTier.PREFIX: 700.0,
    MatchTier.EXACT: 900.0,
}

_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)


@dataclass(frozen=True, slots=True)
class TextMatch:
    """A scored match with character positions in normalized candidate text."""

    tier: MatchTier
    score: float
    positions: tuple[int, ...]
    normalized_query: str
    normalized_candidate: str


def normalize_search_text(value: str) -> str:
    """Normalize case, compatibility characters, and repeated whitespace."""
    if not isinstance(value, str):
        raise TypeError("search text must be a string")
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _query_text(query: str | SearchQuery) -> str:
    if isinstance(query, SearchQuery):
        return query.text
    if not isinstance(query, str):
        raise TypeError("query must be a string or SearchQuery")
    return query


def _bounded_boost(value: float, *, maximum: float, name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be numeric") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return max(0.0, min(maximum, numeric))


def _token_start_positions(query: str, candidate: str) -> tuple[int, ...] | None:
    query_tokens = [match.group(0) for match in _TOKEN_RE.finditer(query)]
    candidate_tokens = list(_TOKEN_RE.finditer(candidate))
    if not query_tokens or not candidate_tokens:
        return None

    positions: list[int] = []
    candidate_index = 0
    for query_token in query_tokens:
        while candidate_index < len(candidate_tokens):
            candidate_match = candidate_tokens[candidate_index]
            candidate_index += 1
            if candidate_match.group(0).startswith(query_token):
                positions.extend(
                    range(
                        candidate_match.start(),
                        candidate_match.start() + len(query_token),
                    )
                )
                break
        else:
            return None
    return tuple(positions)


def _fuzzy_positions(query: str, candidate: str) -> tuple[int, ...] | None:
    query_characters = [character for character in query if character.isalnum()]
    if not query_characters:
        return None

    candidate_characters = [
        (index, character)
        for index, character in enumerate(candidate)
        if character.isalnum()
    ]
    positions: list[int] = []
    candidate_index = 0
    for query_character in query_characters:
        while candidate_index < len(candidate_characters):
            index, candidate_character = candidate_characters[candidate_index]
            candidate_index += 1
            if candidate_character == query_character:
                positions.append(index)
                break
        else:
            return None
    return tuple(positions)


def _quality(*, candidate: str, positions: tuple[int, ...]) -> float:
    if not positions:
        return 0.0
    span = positions[-1] - positions[0] + 1
    gap_count = max(0, span - len(positions))
    unmatched_count = max(0, len(candidate) - len(positions))
    penalty = positions[0] + (gap_count * 2) + min(unmatched_count, 20)
    return max(0.0, MATCH_QUALITY_LIMIT - min(MATCH_QUALITY_LIMIT, penalty))


def _classify(query: str, candidate: str) -> tuple[MatchTier, tuple[int, ...]] | None:
    if query == candidate:
        return MatchTier.EXACT, tuple(range(len(query)))

    if candidate.startswith(query):
        return MatchTier.PREFIX, tuple(range(len(query)))

    token_positions = _token_start_positions(query, candidate)
    if token_positions is not None:
        return MatchTier.TOKEN_START, token_positions

    substring_start = candidate.find(query)
    if substring_start >= 0:
        return (
            MatchTier.SUBSTRING,
            tuple(range(substring_start, substring_start + len(query))),
        )

    fuzzy_positions = _fuzzy_positions(query, candidate)
    if fuzzy_positions is not None:
        return MatchTier.FUZZY, fuzzy_positions
    return None


def match_text(
    query: str | SearchQuery,
    candidate: str,
    *,
    source_boost: float = 0.0,
    state_boost: float = 0.0,
) -> TextMatch | None:
    """Classify and score one candidate.

    Tier gaps are larger than all quality and boost adjustments combined, so a
    fuzzy match can never outrank a substring and a prefix can never outrank an
    exact match solely because of provider-supplied boosts.
    """
    normalized_query = normalize_search_text(_query_text(query))
    normalized_candidate = normalize_search_text(candidate)
    if not normalized_query or not normalized_candidate:
        return None

    classified = _classify(normalized_query, normalized_candidate)
    if classified is None:
        return None
    tier, positions = classified
    score = (
        TIER_BASE_SCORE[tier]
        + _quality(candidate=normalized_candidate, positions=positions)
        + _bounded_boost(
            source_boost,
            maximum=SOURCE_BOOST_LIMIT,
            name="source_boost",
        )
        + _bounded_boost(
            state_boost,
            maximum=STATE_BOOST_LIMIT,
            name="state_boost",
        )
    )
    return TextMatch(
        tier=tier,
        score=score,
        positions=positions,
        normalized_query=normalized_query,
        normalized_candidate=normalized_candidate,
    )


def score_match(
    query: str | SearchQuery,
    candidate: str,
    *,
    source_boost: float = 0.0,
    state_boost: float = 0.0,
) -> float | None:
    """Return only the deterministic score for one candidate."""
    matched = match_text(
        query,
        candidate,
        source_boost=source_boost,
        state_boost=state_boost,
    )
    return matched.score if matched is not None else None


def best_match(
    query: str | SearchQuery,
    candidates: Iterable[str],
    *,
    source_boost: float = 0.0,
    state_boost: float = 0.0,
) -> tuple[str, TextMatch] | None:
    """Return the highest-scoring candidate, preserving input order on ties."""
    best: tuple[str, TextMatch] | None = None
    for candidate in candidates:
        matched = match_text(
            query,
            candidate,
            source_boost=source_boost,
            state_boost=state_boost,
        )
        if matched is None:
            continue
        if best is None or matched.score > best[1].score:
            best = candidate, matched
    return best
