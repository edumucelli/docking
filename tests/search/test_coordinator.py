"""Tests for generation-safe provider coordination and batch merging."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pytest

from docking.search.coordinator import (
    SearchCancelledError,
    SearchCoordinator,
    SearchRequest,
    SearchSnapshot,
    preserve_selected_identity,
)
from docking.search.types import SearchBatch, SearchIdentity, SearchQuery, SearchResult


def _result(
    provider_id: str,
    key: str,
    *,
    score: float = 100.0,
    title: str | None = None,
    canonical_key: str = "",
) -> SearchResult:
    return SearchResult(
        identity=SearchIdentity(provider_id, key),
        title=title or key.title(),
        score=score,
        canonical_key=canonical_key,
    )


class _Provider:
    def __init__(
        self,
        provider_id: str,
        search: Callable[[SearchRequest], Iterable[SearchBatch]] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self._search = search or (lambda _request: ())

    def search(self, request: SearchRequest) -> Iterable[SearchBatch]:
        return self._search(request)


def _coordinator(*provider_ids: str) -> SearchCoordinator:
    return SearchCoordinator(
        (_Provider(provider_id) for provider_id in provider_ids),
        rank_adjuster=lambda _result, _query: 0,
    )


class TestGenerationsAndCancellation:
    def test_recognized_value_is_forwarded_to_provider_request(self):
        coordinator = _coordinator("apps")
        recognized = object()

        request = coordinator.begin("fire", recognized=recognized)

        assert request.recognized is recognized

    def test_generations_increase_and_cancel_the_previous_request(self):
        coordinator = _coordinator("apps")

        first = coordinator.begin("fire")
        second = coordinator.begin("term")

        assert second.generation == first.generation + 1
        assert first.cancelled
        assert not second.cancelled
        assert coordinator.generation == second.generation

    def test_stale_batches_and_errors_are_ignored(self):
        coordinator = _coordinator("apps")
        stale = coordinator.begin("fire")
        current = coordinator.begin("term")

        assert not coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                stale.generation,
                [_result("apps", "firefox")],
            )
        )
        assert not coordinator.record_provider_error(
            provider_id="apps",
            generation=stale.generation,
            error=RuntimeError("old failure"),
        )
        snapshot = coordinator.snapshot()
        assert snapshot.generation == current.generation
        assert snapshot.results == ()
        assert snapshot.errors == ()

    def test_explicit_cancellation_rejects_same_generation_work(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("fire")

        assert coordinator.cancel(request.generation)
        assert not coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                request.generation,
                [_result("apps", "firefox")],
            )
        )
        snapshot = coordinator.snapshot()
        assert snapshot.cancelled
        assert snapshot.is_final

    def test_run_exposes_cooperative_cancellation_to_provider(self):
        observed_cancelled: list[bool] = []

        def search(request: SearchRequest):
            yield SearchBatch.replace(
                "apps",
                request.generation,
                [_result("apps", "first")],
            )
            observed_cancelled.append(request.cancelled)
            request.raise_if_cancelled()
            yield SearchBatch.append(
                "apps",
                request.generation,
                [_result("apps", "too-late")],
            )

        coordinator = SearchCoordinator(
            [_Provider("apps", search)],
            rank_adjuster=lambda _result, _query: 0,
        )

        def cancel_after_first_result(snapshot: SearchSnapshot) -> None:
            if snapshot.results:
                coordinator.cancel(snapshot.generation)

        snapshot = coordinator.run("f", on_update=cancel_after_first_result)

        assert observed_cancelled == [True]
        assert snapshot.cancelled
        assert [result.identity.key for result in snapshot.results] == ["first"]
        assert snapshot.errors == ()


class TestBatchMerging:
    def test_run_provider_allows_event_loop_scheduling_one_source_at_a_time(self):
        def search(request: SearchRequest):
            yield SearchBatch.replace(
                "apps",
                request.generation,
                [_result("apps", "firefox")],
            )

        coordinator = SearchCoordinator(
            [_Provider("apps", search), _Provider("windows")],
            rank_adjuster=lambda _result, _query: 0,
        )
        request = coordinator.begin("fire")

        snapshot = coordinator.run_provider(
            provider_id="apps",
            request=request,
        )

        assert [result.identity.key for result in snapshot.results] == ["firefox"]
        assert snapshot.pending_provider_ids == ("windows",)

    def test_replace_append_update_and_final_have_distinct_semantics(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("a")
        first_a = _result("apps", "a", score=10, title="Old A")
        result_b = _result("apps", "b", score=20)

        assert coordinator.apply_batch(
            SearchBatch.replace("apps", request.generation, [first_a, result_b])
        )
        assert coordinator.apply_batch(
            SearchBatch.append(
                "apps",
                request.generation,
                [
                    _result("apps", "a", score=30, title="New A"),
                    _result("apps", "c", score=15),
                ],
            )
        )
        snapshot = coordinator.snapshot()
        assert [result.identity.key for result in snapshot.results] == ["a", "b", "c"]
        assert snapshot.results[0].title == "New A"

        assert coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                request.generation,
                [_result("apps", "b", score=20)],
            )
        )
        assert [result.identity.key for result in coordinator.snapshot().results] == [
            "b"
        ]

        assert coordinator.finish_provider(
            provider_id="apps",
            generation=request.generation,
        )
        assert coordinator.snapshot().is_final
        assert not coordinator.apply_batch(
            SearchBatch.append(
                "apps",
                request.generation,
                [_result("apps", "late")],
            )
        )

    def test_duplicate_identity_in_one_batch_uses_last_value_once(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("fire")

        coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                request.generation,
                [
                    _result("apps", "firefox", title="Old"),
                    _result("apps", "firefox", title="Current"),
                ],
            )
        )

        assert len(coordinator.snapshot().results) == 1
        assert coordinator.snapshot().results[0].title == "Current"

    def test_same_local_key_from_different_providers_is_not_deduplicated(self):
        coordinator = _coordinator("apps", "windows")
        request = coordinator.begin("fire")

        coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                request.generation,
                [_result("apps", "firefox")],
            )
        )
        coordinator.apply_batch(
            SearchBatch.replace(
                "windows",
                request.generation,
                [_result("windows", "firefox")],
            )
        )

        identities = [result.identity for result in coordinator.snapshot().results]
        assert identities == [
            SearchIdentity("apps", "firefox"),
            SearchIdentity("windows", "firefox"),
        ]

    def test_canonical_key_deduplicates_cross_provider_entities(self):
        coordinator = _coordinator("dock", "recent")
        request = coordinator.begin("proposal")

        coordinator.apply_batch(
            SearchBatch.replace(
                "dock",
                request.generation,
                [
                    _result(
                        "dock",
                        "pinned",
                        score=400,
                        canonical_key="target:file:///tmp/Proposal.pdf",
                    )
                ],
            )
        )
        coordinator.apply_batch(
            SearchBatch.replace(
                "recent",
                request.generation,
                [
                    _result(
                        "recent",
                        "recent",
                        score=300,
                        canonical_key="target:file:///tmp/Proposal.pdf",
                    )
                ],
            )
        )

        assert [result.identity.key for result in coordinator.snapshot().results] == [
            "pinned"
        ]

    def test_query_limit_is_applied_after_global_ranking(self):
        coordinator = _coordinator("apps", "windows")
        request = coordinator.begin(SearchQuery("x", limit=2))

        coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                request.generation,
                [
                    _result("apps", "low", score=10),
                    _result("apps", "high", score=30),
                ],
            )
        )
        coordinator.apply_batch(
            SearchBatch.replace(
                "windows",
                request.generation,
                [_result("windows", "middle", score=20)],
            )
        )

        assert [result.identity.key for result in coordinator.snapshot().results] == [
            "high",
            "middle",
        ]


class TestStableOrderingAndSelection:
    def test_provider_order_breaks_score_ties_independent_of_arrival(self):
        coordinator = _coordinator("apps", "windows")
        request = coordinator.begin("same")

        coordinator.apply_batch(
            SearchBatch.replace(
                "windows",
                request.generation,
                [_result("windows", "second", score=100)],
            )
        )
        coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                request.generation,
                [_result("apps", "first", score=100)],
            )
        )

        assert [result.identity.key for result in coordinator.snapshot().results] == [
            "first",
            "second",
        ]

    def test_first_seen_order_is_stable_across_updates_and_replacements(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("same")

        coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                request.generation,
                [
                    _result("apps", "a", score=100),
                    _result("apps", "b", score=100),
                ],
            )
        )
        coordinator.apply_batch(
            SearchBatch.append(
                "apps",
                request.generation,
                [_result("apps", "b", score=100, title="Updated B")],
            )
        )
        coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                request.generation,
                [
                    _result("apps", "b", score=100),
                    _result("apps", "a", score=100),
                ],
            )
        )

        assert [result.identity.key for result in coordinator.snapshot().results] == [
            "a",
            "b",
        ]

    def test_selected_identity_waits_for_its_provider_then_survives_reordering(self):
        selected = SearchIdentity("apps", "b")
        coordinator = _coordinator("apps", "windows")
        request = coordinator.begin("same", selected_identity=selected)

        coordinator.apply_batch(
            SearchBatch.replace(
                "windows",
                request.generation,
                [_result("windows", "window", score=200)],
            )
        )
        assert coordinator.snapshot().selected_identity is None

        coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                request.generation,
                [
                    _result("apps", "a", score=100),
                    _result("apps", "b", score=50),
                ],
            )
        )
        assert coordinator.snapshot().selected_identity == selected

        coordinator.apply_batch(
            SearchBatch.append(
                "apps",
                request.generation,
                [_result("apps", "b", score=300)],
            )
        )
        assert coordinator.snapshot().selected_identity == selected

        coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                request.generation,
                [_result("apps", "a", score=100)],
            )
        )
        assert coordinator.snapshot().selected_identity == SearchIdentity(
            "windows",
            "window",
        )

    def test_pure_selection_helper_preserves_identity_or_uses_first(self):
        first = _result("apps", "a", score=20)
        second = _result("apps", "b", score=10)

        assert preserve_selected_identity(second.identity, [first, second]) == (
            second.identity
        )
        assert (
            preserve_selected_identity(
                SearchIdentity("apps", "missing"),
                [first, second],
            )
            == first.identity
        )
        assert preserve_selected_identity(first.identity, []) is None


class TestProviderErrors:
    def test_provider_exception_is_captured_and_partial_results_remain(self):
        def failing_search(request: SearchRequest):
            yield SearchBatch.replace(
                "broken",
                request.generation,
                [_result("broken", "partial", score=5)],
            )
            raise RuntimeError("backend unavailable")

        coordinator = SearchCoordinator(
            [
                _Provider(
                    "healthy",
                    lambda request: (
                        SearchBatch.replace(
                            "healthy",
                            request.generation,
                            [_result("healthy", "complete", score=10)],
                        ),
                    ),
                ),
                _Provider("broken", failing_search),
            ],
            rank_adjuster=lambda _result, _query: 0,
        )

        snapshot = coordinator.run("result")

        assert snapshot.is_final
        assert [result.identity.key for result in snapshot.results] == [
            "complete",
            "partial",
        ]
        assert len(snapshot.errors) == 1
        assert snapshot.errors[0].provider_id == "broken"
        assert snapshot.errors[0].error_type == "RuntimeError"
        assert snapshot.errors[0].message == "backend unavailable"

    def test_malformed_cross_provider_batch_becomes_provider_error(self):
        coordinator = SearchCoordinator(
            [
                _Provider(
                    "apps",
                    lambda request: (SearchBatch.final("windows", request.generation),),
                ),
                _Provider("windows"),
            ],
            rank_adjuster=lambda _result, _query: 0,
        )

        snapshot = coordinator.run("fire")

        assert snapshot.is_final
        assert len(snapshot.errors) == 1
        assert snapshot.errors[0].provider_id == "apps"
        assert snapshot.errors[0].error_type == "ValueError"

    def test_unknown_provider_batch_is_rejected(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("fire")

        with pytest.raises(ValueError, match="unknown search provider"):
            coordinator.apply_batch(SearchBatch.final("unknown", request.generation))

    def test_update_callback_failure_is_not_attributed_to_provider(self):
        coordinator = SearchCoordinator(
            [
                _Provider(
                    "apps",
                    lambda request: (
                        SearchBatch.replace(
                            "apps",
                            request.generation,
                            [_result("apps", "firefox")],
                        ),
                    ),
                )
            ],
            rank_adjuster=lambda _result, _query: 0,
        )

        def broken_callback(snapshot: SearchSnapshot) -> None:
            if snapshot.results:
                raise LookupError("presentation failed")

        with pytest.raises(LookupError, match="presentation failed"):
            coordinator.run("fire", on_update=broken_callback)

        snapshot = coordinator.snapshot()
        assert snapshot.errors == ()
        assert snapshot.is_final


class TestSelection:
    """Selection is by identity so a re-ranked list cannot move it."""

    def test_selecting_none_clears_the_selection(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("fire")
        coordinator.apply_batch(
            SearchBatch.replace(
                "apps", request.generation, [_result("apps", "firefox")]
            )
        )
        assert coordinator.select(SearchIdentity("apps", "firefox")) is True

        assert coordinator.select(None) is True
        assert coordinator.snapshot().selected_identity is None

    def test_selecting_an_invisible_identity_is_refused(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("fire")
        coordinator.apply_batch(
            SearchBatch.replace(
                "apps", request.generation, [_result("apps", "firefox")]
            )
        )
        visible = coordinator.snapshot().selected_identity

        assert coordinator.select(SearchIdentity("apps", "absent")) is False
        # The refusal must leave the automatic selection untouched.
        assert coordinator.snapshot().selected_identity == visible

    def test_a_visible_identity_becomes_the_selection(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("fire")
        coordinator.apply_batch(
            SearchBatch.replace(
                "apps", request.generation, [_result("apps", "firefox")]
            )
        )
        identity = SearchIdentity("apps", "firefox")

        assert coordinator.select(identity) is True
        assert coordinator.snapshot().selected_identity == identity

    def test_a_non_identity_is_rejected(self):
        coordinator = _coordinator("apps")

        with pytest.raises(TypeError, match="identity must be a SearchIdentity"):
            coordinator.select("apps:firefox")


class TestRankAdjustmentBounds:
    """A misbehaving ranker must not arbitrarily reorder results.

    The adjustment only influences sort order, so these assert on which result
    ranks first rather than on the stored score.
    """

    def _ranked(self, adjuster, first_score: float, second_score: float) -> list[str]:
        coordinator = SearchCoordinator(
            (_Provider("apps"),),
            rank_adjuster=adjuster,
        )
        request = coordinator.begin("fire")
        coordinator.apply_batch(
            SearchBatch.replace(
                "apps",
                request.generation,
                [
                    _result("apps", "first", score=first_score),
                    _result("apps", "second", score=second_score),
                ],
            )
        )
        return [result.title for result in coordinator.snapshot().results]

    def test_non_finite_adjustments_are_ignored_rather_than_clamped(self):
        # "first" trails by 5. A +inf boost would lift it to the top if it
        # were merely clamped to the limit, so this pins the ignore path.
        order = self._ranked(
            lambda _result, _query: float("inf"),
            first_score=100.0,
            second_score=105.0,
        )

        assert order == ["Second", "First"]

    def test_a_huge_boost_cannot_overtake_a_large_deficit(self):
        # +1000 clamps to +19, so 100 + 19 stays below 130.
        order = self._ranked(
            lambda _result, _query: 1000.0,
            first_score=100.0,
            second_score=130.0,
        )

        assert order == ["Second", "First"]

    def test_a_huge_penalty_cannot_sink_a_large_lead(self):
        # -1000 clamps to -19, so 130 - 19 stays above 100.
        order = self._ranked(
            lambda _result, _query: -1000.0,
            first_score=130.0,
            second_score=100.0,
        )

        assert order == ["First", "Second"]


class TestStateGuards:
    def test_snapshot_before_any_request_is_an_error(self):
        coordinator = _coordinator("apps")

        with pytest.raises(RuntimeError, match="no search generation"):
            coordinator.snapshot()

    def test_cancel_reports_false_when_there_is_nothing_to_cancel(self):
        coordinator = _coordinator("apps")

        assert coordinator.cancel() is False

        request = coordinator.begin("fire")
        assert coordinator.cancel(generation=request.generation + 5) is False
        assert coordinator.cancel() is True
        assert coordinator.cancel() is False

    def test_duplicate_and_invalid_provider_ids_are_rejected(self):
        with pytest.raises(ValueError, match="duplicate search provider"):
            SearchCoordinator(
                (_Provider("apps"), _Provider("apps")),
                rank_adjuster=lambda _result, _query: 0,
            )

        with pytest.raises(
            ValueError, match="search providers must have a non-empty provider_id"
        ):
            SearchCoordinator(
                (_Provider("   "),),
                rank_adjuster=lambda _result, _query: 0,
            )

    def test_begin_rejects_wrong_argument_types(self):
        coordinator = _coordinator("apps")

        with pytest.raises(TypeError, match="query must be a string or SearchQuery"):
            coordinator.begin(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="selected_identity"):
            coordinator.begin("fire", selected_identity="apps:firefox")  # type: ignore[arg-type]

    def test_apply_batch_rejects_non_batches_and_ignores_finished_providers(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("fire")

        with pytest.raises(TypeError, match="batch must be a SearchBatch"):
            coordinator.apply_batch("not-a-batch")  # type: ignore[arg-type]

        assert coordinator.apply_batch(SearchBatch.final("apps", request.generation))
        assert (
            coordinator.apply_batch(
                SearchBatch.append(
                    "apps", request.generation, [_result("apps", "firefox")]
                )
            )
            is False
        )

    def test_apply_batch_rejects_an_unknown_provider(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("fire")

        with pytest.raises(ValueError, match="unknown search provider"):
            coordinator.apply_batch(SearchBatch.final("ghost", request.generation))


class TestProviderErrorRecording:
    def test_records_an_error_for_a_pending_provider(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("fire")

        assert coordinator.record_provider_error(
            provider_id="apps",
            generation=request.generation,
            error=RuntimeError("lookup failed"),
        )
        snapshot = coordinator.snapshot()
        assert [error.provider_id for error in snapshot.errors] == ["apps"]
        assert snapshot.is_final

    def test_rejects_non_exceptions_unknown_providers_and_stale_generations(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("fire")

        with pytest.raises(TypeError, match="error must be an exception"):
            coordinator.record_provider_error(
                provider_id="apps",
                generation=request.generation,
                error="not-an-error",  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError, match="unknown search provider"):
            coordinator.record_provider_error(
                provider_id="ghost",
                generation=request.generation,
                error=RuntimeError("x"),
            )

        coordinator.finish_provider(provider_id="apps", generation=request.generation)
        assert (
            coordinator.record_provider_error(
                provider_id="apps",
                generation=request.generation,
                error=RuntimeError("late"),
            )
            is False
        )


class TestProviderStreamFailures:
    def test_an_unknown_provider_cannot_be_run(self):
        coordinator = _coordinator("apps")
        request = coordinator.begin("fire")

        with pytest.raises(ValueError, match="unknown search provider"):
            coordinator.run_provider(provider_id="ghost", request=request)

    def test_a_stale_request_returns_a_snapshot_without_running(self):
        coordinator = _coordinator("apps")
        stale = coordinator.begin("fire")
        current = coordinator.begin("term")

        snapshot = coordinator.run_provider(provider_id="apps", request=stale)

        assert snapshot.generation == current.generation
        assert snapshot.results == ()

    def test_cancelling_before_the_first_batch_is_not_an_error(self):
        def cancelled(_request):
            raise SearchCancelledError()

        coordinator = SearchCoordinator(
            (_Provider("apps", cancelled),),
            rank_adjuster=lambda _result, _query: 0,
        )

        snapshot = coordinator.run("fire")

        assert snapshot.errors == ()

    def test_failing_to_start_a_stream_is_recorded_as_a_provider_error(self):
        def explode(_request):
            raise RuntimeError("provider boot failed")

        coordinator = SearchCoordinator(
            (_Provider("apps", explode),),
            rank_adjuster=lambda _result, _query: 0,
        )

        snapshot = coordinator.run("fire")

        assert [error.provider_id for error in snapshot.errors] == ["apps"]

    def test_a_misattributed_batch_is_reported_rather_than_crashing(self):
        def wrong_provider(request):
            yield SearchBatch.replace("other", request.generation, [])

        coordinator = SearchCoordinator(
            (_Provider("apps", wrong_provider),),
            rank_adjuster=lambda _result, _query: 0,
        )

        snapshot = coordinator.run("fire")

        assert [error.provider_id for error in snapshot.errors] == ["apps"]
        assert snapshot.results == ()

    def test_a_batch_from_a_stale_generation_is_reported(self):
        def wrong_generation(request):
            yield SearchBatch.replace("apps", request.generation + 1, [])

        coordinator = SearchCoordinator(
            (_Provider("apps", wrong_generation),),
            rank_adjuster=lambda _result, _query: 0,
        )

        snapshot = coordinator.run("fire")

        assert [error.provider_id for error in snapshot.errors] == ["apps"]

    def test_run_stops_consuming_providers_after_a_mid_run_cancel(self):
        consumed: list[str] = []

        def first(request):
            coordinator.cancel()
            consumed.append("first")
            return ()

        def second(request):
            consumed.append("second")
            return ()

        coordinator = SearchCoordinator(
            (_Provider("first", first), _Provider("second", second)),
            rank_adjuster=lambda _result, _query: 0,
        )

        snapshot = coordinator.run("fire")

        assert consumed == ["first"]
        assert snapshot.cancelled is True
