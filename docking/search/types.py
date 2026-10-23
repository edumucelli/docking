"""Define immutable, toolkit-free values shared by every search layer.

Providers, coordinators, services, and GTK presentation communicate through
these dataclasses rather than callbacks or widgets. Construction validates the
invariants once, after which snapshots can be retained, compared, ranked, and
passed between execution contexts safely.

Identities are provider-scoped and stable across reranking. Optional canonical
keys deduplicate the same entity across providers. Actions are data descriptors
whose identities return to the owning provider for invocation. Batches carry a
generation and explicit replace, append, or final semantics so partial work is
unambiguous.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum


def _identity_part(value: str, *, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _string_pairs(
    values: Iterable[tuple[str, str]],
    *,
    name: str,
) -> tuple[tuple[str, str], ...]:
    pairs = tuple(values)
    for key, value in pairs:
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError(f"{name} entries must contain strings")
    return pairs


@dataclass(frozen=True, order=True, slots=True)
class SearchIdentity:
    """Stable provider-scoped identity for a result or action.

    ``key`` only needs to be unique inside ``provider_id``. The pair, rather
    than the display title or list position, is the canonical identity used for
    deduplication and selection preservation.
    """

    provider_id: str
    key: str

    def __post_init__(self) -> None:
        """Normalize and validate both identity components."""
        object.__setattr__(
            self,
            "provider_id",
            _identity_part(self.provider_id, name="provider_id"),
        )
        object.__setattr__(self, "key", _identity_part(self.key, name="key"))

    @property
    def canonical(self) -> tuple[str, str]:
        """Return the unambiguous canonical key."""
        return self.provider_id, self.key

    @property
    def local_id(self) -> str:
        """Alias documenting that ``key`` is local to one provider."""
        return self.key


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """One bounded search request plus string-only provider context.

    Context is an immutable tuple rather than an open mutable mapping. This
    keeps requests deterministic and prevents providers from communicating by
    mutating shared query state.
    """

    text: str
    limit: int = 50
    context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Validate the limit and freeze string-only context pairs."""
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise TypeError("limit must be an integer")
        if self.limit <= 0:
            raise ValueError("limit must be greater than zero")
        object.__setattr__(
            self,
            "context",
            _string_pairs(self.context, name="context"),
        )

    @property
    def is_empty(self) -> bool:
        """Whether the query contains no non-whitespace text."""
        return not self.text.strip()

    def context_value(self, key: str, default: str = "") -> str:
        """Return one context value without exposing mutable query state."""
        return dict(self.context).get(key, default)


@dataclass(frozen=True, slots=True)
class SearchAction:
    """Serializable action descriptor owned by a search provider.

    Providers receive the action identity back when the UI activates it.
    ``verb`` and ``payload`` are plain data for presentation or dispatch; this
    type deliberately has no callable field and therefore cannot retain GTK
    objects or callbacks.
    """

    identity: SearchIdentity
    label: str
    verb: str = "activate"
    payload: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Validate action identity, label, verb, and immutable payload."""
        if not isinstance(self.identity, SearchIdentity):
            raise TypeError("identity must be a SearchIdentity")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("label must not be empty")
        if not isinstance(self.verb, str) or not self.verb.strip():
            raise ValueError("verb must not be empty")
        object.__setattr__(
            self,
            "payload",
            _string_pairs(self.payload, name="payload"),
        )

    @property
    def canonical_identity(self) -> SearchIdentity:
        """Return the stable provider-scoped action identity."""
        return self.identity


@dataclass(frozen=True, slots=True)
class SearchPreview:
    """Provider-neutral preview shown beside search results."""

    title: str
    body: str
    kind: str = "text"
    target: str = ""

    def __post_init__(self) -> None:
        """Validate required display text and target transport values."""
        if not self.title.strip():
            raise ValueError("preview title must not be empty")
        if not isinstance(self.body, str):
            raise TypeError("preview body must be a string")
        if not isinstance(self.target, str):
            raise TypeError("preview target must be a string")


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One immutable ranked result emitted by exactly one provider.

    ``score`` is the provider's base relevance. The coordinator may apply a
    small bounded learning adjustment without mutating the result. Actions
    must use the same provider ID so invocation always returns to the component
    that created the descriptor.
    """

    identity: SearchIdentity
    title: str
    description: str = ""
    score: float = 0.0
    icon_name: str | None = None
    source: str = ""
    state: str = ""
    keywords: tuple[str, ...] = ()
    actions: tuple[SearchAction, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()
    preview: SearchPreview | None = None
    canonical_key: str = ""

    def __post_init__(self) -> None:
        """Validate score, provider ownership, and immutable child values."""
        if not isinstance(self.identity, SearchIdentity):
            raise TypeError("identity must be a SearchIdentity")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title must not be empty")
        score = float(self.score)
        if not math.isfinite(score):
            raise ValueError("score must be finite")
        object.__setattr__(self, "score", score)

        keywords = tuple(self.keywords)
        if not all(isinstance(keyword, str) for keyword in keywords):
            raise TypeError("keywords must contain strings")
        object.__setattr__(self, "keywords", keywords)

        actions = tuple(self.actions)
        action_identities: set[SearchIdentity] = set()
        for action in actions:
            if not isinstance(action, SearchAction):
                raise TypeError("actions must contain SearchAction values")
            if action.identity.provider_id != self.identity.provider_id:
                raise ValueError("result actions must use the result provider")
            if action.identity in action_identities:
                raise ValueError("action identities must be unique within a result")
            action_identities.add(action.identity)
        object.__setattr__(self, "actions", actions)
        object.__setattr__(
            self,
            "metadata",
            _string_pairs(self.metadata, name="metadata"),
        )
        if not isinstance(self.canonical_key, str):
            raise TypeError("canonical_key must be a string")
        if self.preview is not None and not isinstance(self.preview, SearchPreview):
            raise TypeError("preview must be a SearchPreview or None")

    @property
    def canonical_identity(self) -> SearchIdentity:
        """Return the canonical identity used for result deduplication."""
        return self.identity

    @property
    def deduplication_key(self) -> str:
        """Return a cross-provider entity key when one is available."""
        return self.canonical_key or "\x1f".join(self.identity.canonical)


class SearchBatchKind(str, Enum):
    """How a provider batch changes its current generation results."""

    REPLACE = "replace"
    APPEND = "append"
    FINAL = "final"


@dataclass(frozen=True, slots=True)
class SearchBatch:
    """One generation-tagged provider update.

    ``REPLACE`` discards the provider's prior results, ``APPEND`` adds or
    updates results by canonical identity, and ``FINAL`` marks the provider
    complete without changing results.
    """

    provider_id: str
    generation: int
    kind: SearchBatchKind
    results: tuple[SearchResult, ...] = ()

    def __post_init__(self) -> None:
        """Validate generation, provider ownership, and batch semantics."""
        object.__setattr__(
            self,
            "provider_id",
            _identity_part(self.provider_id, name="provider_id"),
        )
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise TypeError("generation must be an integer")
        if self.generation <= 0:
            raise ValueError("generation must be greater than zero")
        object.__setattr__(self, "kind", SearchBatchKind(self.kind))

        results = tuple(self.results)
        if self.kind is SearchBatchKind.FINAL and results:
            raise ValueError("final batches must not contain results")
        for result in results:
            if not isinstance(result, SearchResult):
                raise TypeError("results must contain SearchResult values")
            if result.identity.provider_id != self.provider_id:
                raise ValueError("batch results must use the batch provider")
        object.__setattr__(self, "results", results)

    @classmethod
    def replace(
        cls,
        provider_id: str,
        generation: int,
        results: Iterable[SearchResult] = (),
    ) -> SearchBatch:
        """Build a batch that replaces all prior provider results."""
        return cls(
            provider_id=provider_id,
            generation=generation,
            kind=SearchBatchKind.REPLACE,
            results=tuple(results),
        )

    @classmethod
    def append(
        cls,
        provider_id: str,
        generation: int,
        results: Iterable[SearchResult] = (),
    ) -> SearchBatch:
        """Build a batch that adds or updates provider results."""
        return cls(
            provider_id=provider_id,
            generation=generation,
            kind=SearchBatchKind.APPEND,
            results=tuple(results),
        )

    @classmethod
    def final(cls, provider_id: str, generation: int) -> SearchBatch:
        """Build the completion marker for a provider generation."""
        return cls(
            provider_id=provider_id,
            generation=generation,
            kind=SearchBatchKind.FINAL,
        )

    @property
    def replaces(self) -> bool:
        """Return whether this batch replaces the provider's prior results."""
        return self.kind is SearchBatchKind.REPLACE

    @property
    def appends(self) -> bool:
        """Return whether this batch adds to the provider's prior results."""
        return self.kind is SearchBatchKind.APPEND

    @property
    def is_final(self) -> bool:
        """Return whether this batch marks its provider complete."""
        return self.kind is SearchBatchKind.FINAL
