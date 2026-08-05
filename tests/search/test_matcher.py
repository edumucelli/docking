"""Tests for deterministic global-search matching and scoring."""

from __future__ import annotations

import pytest

from docking.search.matcher import (
    SOURCE_BOOST_LIMIT,
    STATE_BOOST_LIMIT,
    MatchTier,
    best_match,
    match_text,
    normalize_search_text,
    score_match,
)
from docking.search.types import SearchQuery


def _match(query: str, candidate: str, **kwargs):
    matched = match_text(query, candidate, **kwargs)
    assert matched is not None
    return matched


class TestMatchTiers:
    def test_exact_prefix_token_substring_and_fuzzy_tiers_are_ordered(self):
        exact = _match("abc", "ABC")
        prefix = _match("abc", "abcdef")
        token = _match("abc", "zero abcdef")
        substring = _match("abc", "zzabczz")
        fuzzy = _match("abc", "a-x-b-x-c")

        assert exact.tier is MatchTier.EXACT
        assert prefix.tier is MatchTier.PREFIX
        assert token.tier is MatchTier.TOKEN_START
        assert substring.tier is MatchTier.SUBSTRING
        assert fuzzy.tier is MatchTier.FUZZY
        assert exact.score > prefix.score > token.score > substring.score > fuzzy.score

    def test_multiple_query_tokens_match_successive_token_starts(self):
        matched = _match("foo ba", "zero food basket")

        assert matched.tier is MatchTier.TOKEN_START
        assert matched.positions == (5, 6, 7, 10, 11)

    def test_fuzzy_match_requires_query_characters_in_order(self):
        assert match_text("abc", "acb") is None
        assert _match("abc", "a---b---c").tier is MatchTier.FUZZY

    def test_empty_query_or_candidate_does_not_match(self):
        assert match_text("", "Firefox") is None
        assert match_text("fire", " ") is None


class TestNormalization:
    def test_matching_is_casefolded_and_whitespace_normalized(self):
        matched = _match("  STRASSE ", "Straße")

        assert matched.tier is MatchTier.EXACT
        assert matched.normalized_query == "strasse"
        assert matched.normalized_candidate == "strasse"

    def test_normalizer_applies_unicode_compatibility(self):
        assert normalize_search_text("Ｆｉｒｅ  Fox") == "fire fox"

    def test_search_query_value_is_accepted(self):
        matched = match_text(SearchQuery("fire"), "Firefox")

        assert matched is not None
        assert matched.tier is MatchTier.PREFIX


class TestScoreQualityAndBoosts:
    def test_tighter_fuzzy_match_scores_higher(self):
        tight = _match("abc", "axbyc")
        loose = _match("abc", "a---b---c")

        assert tight.tier is loose.tier is MatchTier.FUZZY
        assert tight.score > loose.score

    def test_earlier_substring_scores_higher(self):
        early = _match("abc", "xabc")
        late = _match("abc", "zzabc")

        assert early.tier is late.tier is MatchTier.SUBSTRING
        assert early.score > late.score

    def test_source_and_state_boosts_are_clamped(self):
        unboosted = _match("abc", "a-x-b-x-c")
        boosted = _match(
            "abc",
            "a-x-b-x-c",
            source_boost=10_000,
            state_boost=10_000,
        )

        assert boosted.score - unboosted.score == (
            SOURCE_BOOST_LIMIT + STATE_BOOST_LIMIT
        )

    def test_negative_boosts_do_not_reduce_a_match(self):
        baseline = score_match("abc", "abcdef")
        negative = score_match(
            "abc",
            "abcdef",
            source_boost=-50,
            state_boost=-20,
        )

        assert negative == baseline

    def test_bounded_boosts_cannot_cross_match_tiers(self):
        fuzzy = _match(
            "abc",
            "a-x-b-x-c",
            source_boost=10_000,
            state_boost=10_000,
        )
        substring = _match("abc", "zzabczz")

        assert fuzzy.score < substring.score

    def test_non_finite_boost_is_rejected(self):
        with pytest.raises(ValueError, match="finite"):
            match_text("abc", "abc", source_boost=float("inf"))


class TestDeterminism:
    def test_equal_scores_preserve_input_order(self):
        first = best_match("firefox", ["FIREFOX", "Firefox"])
        second = best_match("firefox", ["Firefox", "FIREFOX"])

        assert first is not None
        assert second is not None
        assert first[0] == "FIREFOX"
        assert second[0] == "Firefox"

    def test_repeated_calls_return_identical_match_data(self):
        first = match_text("fbr", "Firefox Browser")
        second = match_text("fbr", "Firefox Browser")

        assert first == second
