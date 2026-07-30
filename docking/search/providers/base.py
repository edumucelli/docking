"""Shared contracts and matching helpers for built-in search providers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from docking.search.coordinator import SearchProvider, SearchRequest
from docking.search.matcher import best_match
from docking.search.types import SearchAction, SearchIdentity, SearchResult


@runtime_checkable
class InvokableSearchProvider(SearchProvider, Protocol):
    """A provider that can execute its transport-neutral action descriptors."""

    def invoke(
        self,
        *,
        result_identity: SearchIdentity,
        action_identity: SearchIdentity,
    ) -> bool: ...


def metadata(**values: str) -> tuple[tuple[str, str], ...]:
    return tuple((key, value) for key, value in values.items() if value)


def metadata_dict(result: SearchResult) -> dict[str, str]:
    return dict(result.metadata)


def action(
    *,
    provider_id: str,
    entity_id: str,
    action_id: str,
    label: str,
    verb: str = "activate",
) -> SearchAction:
    return SearchAction(
        identity=SearchIdentity(provider_id, f"{entity_id}\x1f{action_id}"),
        label=label,
        verb=verb,
        payload=(("entity_id", entity_id), ("action_id", action_id)),
    )


def action_parts(identity: SearchIdentity) -> tuple[str, str] | None:
    parts = identity.key.rsplit("\x1f", 1)
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def score_fields(
    request: SearchRequest,
    fields: Iterable[str],
    *,
    source_boost: float = 0,
    state_boost: float = 0,
) -> float | None:
    matched = best_match(
        request.query,
        (field for field in fields if field),
        source_boost=source_boost,
        state_boost=state_boost,
    )
    return matched[1].score if matched is not None else None


def is_special_query(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith(("=", "/", "~/", "./", "../", "file://"))


__all__ = [
    "InvokableSearchProvider",
    "action",
    "action_parts",
    "is_special_query",
    "metadata",
    "metadata_dict",
    "score_fields",
]
