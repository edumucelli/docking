"""Tests for privacy-preserving MRU search relevance."""

from __future__ import annotations

import json

from docking.search.coordinator import SearchCoordinator
from docking.search.types import (
    SearchAction,
    SearchBatch,
    SearchIdentity,
    SearchQuery,
    SearchResult,
)
from docking.search.usage import MAX_RANK_BOOST, SearchUsageStore


def _result(key: str, score: float = 100) -> SearchResult:
    return SearchResult(
        identity=SearchIdentity("apps", key),
        title=key,
        score=score,
        actions=(
            SearchAction(SearchIdentity("apps", f"{key}/open"), "Open"),
            SearchAction(SearchIdentity("apps", f"{key}/pin"), "Pin"),
            SearchAction(SearchIdentity("apps", f"{key}/close"), "Close"),
        ),
    )


def test_usage_state_hashes_queries_and_identities(tmp_path) -> None:
    path = tmp_path / "usage.json"
    store = SearchUsageStore(path=path)
    result = _result("/private/report.txt")

    store.record(
        query="confidential report",
        result=result,
        action=result.actions[1],
        now=100,
    )
    payload = path.read_text()

    assert "confidential" not in payload
    assert "/private/report.txt" not in payload
    assert json.loads(payload)["version"] == 1


def test_frequency_and_recency_produce_a_bounded_boost(tmp_path) -> None:
    store = SearchUsageStore(path=tmp_path / "usage.json")
    result = _result("firefox")
    for count in range(20):
        store.record(
            query="fire",
            result=result,
            action=result.actions[0],
            now=1_000 + count,
        )

    boost = store.boost(result, SearchQuery("fire"), now=1_100)

    assert 0 < boost <= MAX_RANK_BOOST


def test_secondary_actions_learn_without_replacing_primary(tmp_path) -> None:
    store = SearchUsageStore(path=tmp_path / "usage.json")
    result = _result("firefox")
    for count in range(3):
        store.record(
            query="fire",
            result=result,
            action=result.actions[2],
            now=1_000 + count,
        )

    ranked = store.rank_actions(result.actions)

    assert ranked[0].label == "Open"
    assert ranked[1].label == "Close"


class _Provider:
    provider_id = "apps"

    def search(self, request):
        yield SearchBatch.replace(
            self.provider_id,
            request.generation,
            (
                _result("first", score=100),
                _result("learned", score=100),
            ),
        )


def test_coordinator_applies_learned_rank_adjustment(tmp_path) -> None:
    store = SearchUsageStore(path=tmp_path / "usage.json")
    learned = _result("learned")
    store.record(
        query="same",
        result=learned,
        action=learned.actions[0],
        now=100,
    )
    coordinator = SearchCoordinator(
        (_Provider(),),
        rank_adjuster=lambda result, query: store.boost(
            result,
            query,
            now=101,
        ),
    )

    snapshot = coordinator.run("same")

    assert [result.identity.key for result in snapshot.results] == [
        "learned",
        "first",
    ]


def test_malformed_nonfinite_usage_state_is_ignored(tmp_path) -> None:
    path = tmp_path / "usage.json"
    path.write_text('{"results":{"bad":{"count":"Infinity","last_used":"Infinity"}}}')

    store = SearchUsageStore(path=path)

    assert store.boost(_result("firefox"), SearchQuery("fire")) == 0
