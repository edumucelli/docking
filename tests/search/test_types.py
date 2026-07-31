"""Tests for immutable search value objects and batch semantics."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from docking.search.types import (
    SearchAction,
    SearchBatch,
    SearchBatchKind,
    SearchIdentity,
    SearchPreview,
    SearchQuery,
    SearchResult,
)


def _result(provider_id: str = "apps", key: str = "firefox") -> SearchResult:
    return SearchResult(
        identity=SearchIdentity(provider_id, key),
        title="Firefox",
    )


class TestSearchIdentity:
    def test_identity_is_provider_scoped_and_hashable(self):
        apps = SearchIdentity("apps", "firefox")
        windows = SearchIdentity("windows", "firefox")

        assert apps != windows
        assert apps.canonical == ("apps", "firefox")
        assert len({apps, windows, SearchIdentity("apps", "firefox")}) == 2

    def test_identity_is_frozen(self):
        identity = SearchIdentity("apps", "firefox")

        with pytest.raises(FrozenInstanceError):
            identity.key = "changed"  # ty: ignore[invalid-assignment]

    def test_identity_rejects_empty_components(self):
        with pytest.raises(ValueError, match="provider_id"):
            SearchIdentity("", "firefox")
        with pytest.raises(ValueError, match="key"):
            SearchIdentity("apps", " ")


class TestSearchValues:
    def test_query_reports_whitespace_as_empty_and_validates_limit(self):
        assert SearchQuery(" \t").is_empty
        assert not SearchQuery("fire").is_empty
        query = SearchQuery(
            "fire",
            context=(("intent_kind", "global"),),
        )
        assert query.context_value("intent_kind") == "global"
        assert query.context_value("missing", "fallback") == "fallback"

        with pytest.raises(ValueError, match="greater than zero"):
            SearchQuery("fire", limit=0)

    def test_result_converts_collections_to_immutable_tuples(self):
        action = SearchAction(
            identity=SearchIdentity("apps", "firefox/open"),
            label="Open",
            payload=cast(
                tuple[tuple[str, str], ...],
                [("desktop_id", "firefox.desktop")],
            ),
        )
        result = SearchResult(
            identity=SearchIdentity("apps", "firefox"),
            title="Firefox",
            keywords=cast(tuple[str, ...], ["browser", "web"]),
            actions=cast(tuple[SearchAction, ...], [action]),
            metadata=cast(tuple[tuple[str, str], ...], [("category", "Network")]),
            preview=SearchPreview(title="Firefox", body="Web Browser"),
        )

        assert result.keywords == ("browser", "web")
        assert result.actions == (action,)
        assert result.metadata == (("category", "Network"),)
        assert result.preview is not None
        assert result.preview.body == "Web Browser"
        assert action.payload == (("desktop_id", "firefox.desktop"),)

    def test_result_rejects_cross_provider_action_identity(self):
        action = SearchAction(
            identity=SearchIdentity("windows", "firefox/open"),
            label="Open",
        )

        with pytest.raises(ValueError, match="result provider"):
            SearchResult(
                identity=SearchIdentity("apps", "firefox"),
                title="Firefox",
                actions=(action,),
            )

    def test_result_rejects_non_finite_score(self):
        with pytest.raises(ValueError, match="finite"):
            SearchResult(
                identity=SearchIdentity("apps", "firefox"),
                title="Firefox",
                score=float("nan"),
            )


class TestSearchBatch:
    def test_factories_make_explicit_replace_append_and_final_batches(self):
        result = _result()

        replacement = SearchBatch.replace("apps", 3, [result])
        addition = SearchBatch.append("apps", 3, [result])
        final = SearchBatch.final("apps", 3)

        assert replacement.kind is SearchBatchKind.REPLACE
        assert replacement.replaces
        assert replacement.results == (result,)
        assert addition.kind is SearchBatchKind.APPEND
        assert addition.appends
        assert final.kind is SearchBatchKind.FINAL
        assert final.is_final
        assert final.results == ()

    def test_batch_rejects_results_from_another_provider(self):
        with pytest.raises(ValueError, match="batch provider"):
            SearchBatch.replace("apps", 1, [_result("windows")])

    def test_final_batch_rejects_results(self):
        with pytest.raises(ValueError, match="must not contain"):
            SearchBatch(
                provider_id="apps",
                generation=1,
                kind=SearchBatchKind.FINAL,
                results=(_result(),),
            )
