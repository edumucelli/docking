"""Generation-safe coordination of toolkit-free search providers."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from docking.search.types import (
    SearchBatch,
    SearchBatchKind,
    SearchIdentity,
    SearchQuery,
    SearchResult,
)


class SearchCancelledError(RuntimeError):
    """Raised by a provider that stops at a cooperative cancellation point."""


RankAdjuster = Callable[[SearchResult, SearchQuery], float]
RANK_ADJUSTMENT_LIMIT = 19.0


class SearchCancellation:
    """Thread-safe cooperative cancellation token for one generation."""

    __slots__ = ("_event", "generation")

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise SearchCancelledError(
                f"search generation {self.generation} was cancelled"
            )


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """Request passed to each provider for one coordinator generation.

    ``recognized`` carries an optional value produced during intent routing so
    the selected built-in provider does not need to parse the same query again.
    """

    query: SearchQuery
    generation: int
    cancellation: SearchCancellation
    recognized: object | None = None

    @property
    def cancelled(self) -> bool:
        return self.cancellation.cancelled

    def raise_if_cancelled(self) -> None:
        self.cancellation.raise_if_cancelled()


@runtime_checkable
class SearchProvider(Protocol):
    """Pure provider contract consumed by :class:`SearchCoordinator`."""

    provider_id: str

    def search(self, request: SearchRequest) -> Iterable[SearchBatch]:
        """Yield replace/append/final batches for ``request``."""
        ...


@dataclass(frozen=True, slots=True)
class ProviderError:
    """Serializable provider failure captured without retaining an exception."""

    provider_id: str
    generation: int
    error_type: str
    message: str

    @classmethod
    def from_exception(
        cls,
        *,
        provider_id: str,
        generation: int,
        error: BaseException,
    ) -> ProviderError:
        return cls(
            provider_id=provider_id,
            generation=generation,
            error_type=type(error).__name__,
            message=str(error) or type(error).__name__,
        )


@dataclass(frozen=True, slots=True)
class SearchSnapshot:
    """Immutable merged state suitable for consumption by any UI layer."""

    generation: int
    query: SearchQuery
    results: tuple[SearchResult, ...]
    selected_identity: SearchIdentity | None
    pending_provider_ids: tuple[str, ...]
    errors: tuple[ProviderError, ...]
    cancelled: bool = False

    @property
    def is_final(self) -> bool:
        return not self.pending_provider_ids

    def result_for(self, identity: SearchIdentity) -> SearchResult | None:
        return next(
            (result for result in self.results if result.identity == identity),
            None,
        )


@dataclass(frozen=True, slots=True)
class _ResultEntry:
    result: SearchResult
    provider_order: int
    first_seen: int


def preserve_selected_identity(
    selected_identity: SearchIdentity | None,
    results: Iterable[SearchResult],
) -> SearchIdentity | None:
    """Keep a selected identity when present, otherwise select the first result."""
    result_tuple = tuple(results)
    if selected_identity is not None and any(
        result.identity == selected_identity for result in result_tuple
    ):
        return selected_identity
    return result_tuple[0].identity if result_tuple else None


class SearchCoordinator:
    """Merge provider streams while rejecting cancelled and stale work.

    The coordinator deliberately does not create worker threads or depend on a
    GUI event loop. Callers may run ``SearchProvider.search`` wherever
    appropriate and feed batches through :meth:`apply_batch`, or use
    :meth:`run` for deterministic synchronous execution.
    """

    def __init__(
        self,
        providers: Iterable[SearchProvider],
        *,
        rank_adjuster: RankAdjuster,
    ) -> None:
        self._providers = tuple(providers)
        self._rank_adjuster = rank_adjuster
        self._provider_by_id: dict[str, SearchProvider] = {}
        self._provider_order: dict[str, int] = {}
        for index, provider in enumerate(self._providers):
            provider_id = self._validated_provider_id(provider)
            if provider_id in self._provider_by_id:
                raise ValueError(f"duplicate search provider: {provider_id}")
            self._provider_by_id[provider_id] = provider
            self._provider_order[provider_id] = index

        self._lock = threading.RLock()
        self._generation = 0
        self._request: SearchRequest | None = None
        self._results_by_provider: dict[
            str,
            dict[SearchIdentity, _ResultEntry],
        ] = {}
        self._first_seen_by_identity: dict[SearchIdentity, int] = {}
        self._next_first_seen = 0
        self._pending_provider_ids: set[str] = set()
        self._errors_by_provider: dict[str, ProviderError] = {}
        self._selection_target: SearchIdentity | None = None
        self._selected_identity: SearchIdentity | None = None
        self._waiting_for_initial_selection = False

    @staticmethod
    def _validated_provider_id(provider: SearchProvider) -> str:
        provider_id = getattr(provider, "provider_id", None)
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("search providers must have a non-empty provider_id")
        if provider_id != provider_id.strip():
            raise ValueError("search provider_id must not have surrounding whitespace")
        return provider_id

    @property
    def providers(self) -> tuple[SearchProvider, ...]:
        return self._providers

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def request(self) -> SearchRequest | None:
        with self._lock:
            return self._request

    def begin(
        self,
        query: str | SearchQuery,
        *,
        selected_identity: SearchIdentity | None = None,
        recognized: object | None = None,
    ) -> SearchRequest:
        """Cancel the previous request and start the next generation."""
        normalized_query = SearchQuery(query) if isinstance(query, str) else query
        if not isinstance(normalized_query, SearchQuery):
            raise TypeError("query must be a string or SearchQuery")
        if selected_identity is not None and not isinstance(
            selected_identity,
            SearchIdentity,
        ):
            raise TypeError("selected_identity must be a SearchIdentity or None")

        with self._lock:
            if self._request is not None:
                self._request.cancellation.cancel()
            self._generation += 1
            cancellation = SearchCancellation(self._generation)
            request = SearchRequest(
                query=normalized_query,
                generation=self._generation,
                cancellation=cancellation,
                recognized=recognized,
            )
            self._request = request
            self._results_by_provider = {
                provider_id: {} for provider_id in self._provider_by_id
            }
            self._first_seen_by_identity.clear()
            self._next_first_seen = 0
            self._pending_provider_ids = set(self._provider_by_id)
            self._errors_by_provider.clear()
            self._selection_target = selected_identity
            self._selected_identity = None
            self._waiting_for_initial_selection = selected_identity is not None
            return request

    def start(
        self,
        query: str | SearchQuery,
        *,
        selected_identity: SearchIdentity | None = None,
        recognized: object | None = None,
    ) -> SearchRequest:
        """Alias for :meth:`begin` for callers that prefer lifecycle wording."""
        return self.begin(
            query,
            selected_identity=selected_identity,
            recognized=recognized,
        )

    def cancel(self, generation: int | None = None) -> bool:
        """Cancel the current generation and reject all subsequent batches."""
        with self._lock:
            request = self._request
            if request is None:
                return False
            if generation is not None and generation != request.generation:
                return False
            if request.cancelled:
                return False
            request.cancellation.cancel()
            self._pending_provider_ids.clear()
            self._refresh_selection_unlocked()
            return True

    def is_current(self, generation: int) -> bool:
        with self._lock:
            return (
                self._request is not None
                and not self._request.cancelled
                and generation == self._generation
            )

    def apply_batch(self, batch: SearchBatch) -> bool:
        """Apply a current batch, returning false for stale or completed work."""
        if not isinstance(batch, SearchBatch):
            raise TypeError("batch must be a SearchBatch")
        with self._lock:
            request = self._request
            if (
                request is None
                or request.cancelled
                or batch.generation != request.generation
            ):
                return False
            if batch.provider_id not in self._provider_by_id:
                raise ValueError(f"unknown search provider: {batch.provider_id}")
            if batch.provider_id not in self._pending_provider_ids:
                return False

            if batch.kind is SearchBatchKind.FINAL:
                self._pending_provider_ids.remove(batch.provider_id)
                self._refresh_selection_unlocked()
                return True

            provider_results = self._results_by_provider[batch.provider_id]
            if batch.kind is SearchBatchKind.REPLACE:
                provider_results = {}

            for result in batch.results:
                identity = result.canonical_identity
                first_seen = self._first_seen_by_identity.get(identity)
                if first_seen is None:
                    first_seen = self._next_first_seen
                    self._next_first_seen += 1
                    self._first_seen_by_identity[identity] = first_seen
                provider_results[identity] = _ResultEntry(
                    result=result,
                    provider_order=self._provider_order[batch.provider_id],
                    first_seen=first_seen,
                )
            self._results_by_provider[batch.provider_id] = provider_results
            self._refresh_selection_unlocked()
            return True

    def finish_provider(self, *, provider_id: str, generation: int) -> bool:
        """Mark a provider complete without changing its accumulated results."""
        return self.apply_batch(SearchBatch.final(provider_id, generation))

    def record_provider_error(
        self,
        *,
        provider_id: str,
        generation: int,
        error: BaseException,
    ) -> bool:
        """Capture a current provider failure and mark that provider complete."""
        if not isinstance(error, BaseException):
            raise TypeError("error must be an exception")
        with self._lock:
            request = self._request
            if request is None or request.cancelled or generation != request.generation:
                return False
            if provider_id not in self._provider_by_id:
                raise ValueError(f"unknown search provider: {provider_id}")
            if provider_id not in self._pending_provider_ids:
                return False
            self._errors_by_provider[provider_id] = ProviderError.from_exception(
                provider_id=provider_id,
                generation=generation,
                error=error,
            )
            self._pending_provider_ids.remove(provider_id)
            self._refresh_selection_unlocked()
            return True

    def select(self, identity: SearchIdentity | None) -> bool:
        """Update selection by identity, never by a transient list index."""
        if identity is not None and not isinstance(identity, SearchIdentity):
            raise TypeError("identity must be a SearchIdentity or None")
        with self._lock:
            if identity is None:
                self._selection_target = None
                self._selected_identity = None
                self._waiting_for_initial_selection = False
                return True
            visible_identities = {
                result.identity for result in self._ranked_results_unlocked()
            }
            if identity not in visible_identities:
                return False
            self._selection_target = identity
            self._selected_identity = identity
            self._waiting_for_initial_selection = False
            return True

    def snapshot(self) -> SearchSnapshot:
        """Return the current immutable merged state."""
        with self._lock:
            request = self._request
            if request is None:
                raise RuntimeError("no search generation has been started")
            results = self._ranked_results_unlocked()
            pending = tuple(
                provider_id
                for provider_id in self._provider_by_id
                if provider_id in self._pending_provider_ids
            )
            errors = tuple(
                self._errors_by_provider[provider_id]
                for provider_id in self._provider_by_id
                if provider_id in self._errors_by_provider
            )
            return SearchSnapshot(
                generation=request.generation,
                query=request.query,
                results=results,
                selected_identity=self._selected_identity,
                pending_provider_ids=pending,
                errors=errors,
                cancelled=request.cancelled,
            )

    def run(
        self,
        query: str | SearchQuery,
        *,
        selected_identity: SearchIdentity | None = None,
        on_update: Callable[[SearchSnapshot], None] | None = None,
    ) -> SearchSnapshot:
        """Run all providers synchronously and return their final merged state."""
        request = self.begin(query, selected_identity=selected_identity)
        self._publish(on_update)
        for provider in self._providers:
            if request.cancelled or not self.is_current(request.generation):
                break
            self._consume_provider(
                provider=provider,
                request=request,
                on_update=on_update,
            )
        return self.snapshot()

    def run_provider(
        self,
        *,
        provider_id: str,
        request: SearchRequest,
        on_update: Callable[[SearchSnapshot], None] | None = None,
    ) -> SearchSnapshot:
        """Consume one registered provider for a current request.

        UI integrations can schedule providers one at a time on their event
        loop while retaining the same generation, cancellation, and stream
        validation used by :meth:`run`.
        """
        provider = self._provider_by_id.get(provider_id)
        if provider is None:
            raise ValueError(f"unknown search provider: {provider_id}")
        if request is not self.request or not self.is_current(request.generation):
            return self.snapshot()
        self._consume_provider(
            provider=provider,
            request=request,
            on_update=on_update,
        )
        return self.snapshot()

    def _consume_provider(
        self,
        *,
        provider: SearchProvider,
        request: SearchRequest,
        on_update: Callable[[SearchSnapshot], None] | None,
    ) -> None:
        provider_id = provider.provider_id
        try:
            batches = iter(provider.search(request))
        except SearchCancelledError:
            batches = iter(())
        except Exception as error:
            if self.record_provider_error(
                provider_id=provider_id,
                generation=request.generation,
                error=error,
            ):
                self._publish(on_update)
            return

        while True:
            try:
                batch = next(batches)
                request.raise_if_cancelled()
                if batch.provider_id != provider_id:
                    raise ValueError(
                        f"provider {provider_id} emitted a batch for "
                        f"{batch.provider_id}"
                    )
                if batch.generation != request.generation:
                    raise ValueError(
                        f"provider {provider_id} emitted generation "
                        f"{batch.generation}, expected {request.generation}"
                    )
                accepted = self.apply_batch(batch)
            except StopIteration:
                break
            except SearchCancelledError:
                break
            except Exception as error:
                if self.record_provider_error(
                    provider_id=provider_id,
                    generation=request.generation,
                    error=error,
                ):
                    self._publish(on_update)
                return

            if accepted:
                try:
                    self._publish(on_update)
                except Exception:
                    self.finish_provider(
                        provider_id=provider_id,
                        generation=request.generation,
                    )
                    raise

        if self.is_current(request.generation) and self.finish_provider(
            provider_id=provider_id,
            generation=request.generation,
        ):
            self._publish(on_update)

    def _ranked_results_unlocked(self) -> tuple[SearchResult, ...]:
        entries = [
            entry
            for provider_results in self._results_by_provider.values()
            for entry in provider_results.values()
        ]
        entries.sort(
            key=lambda entry: (
                -self._ranked_score(entry.result),
                entry.provider_order,
                entry.first_seen,
                entry.result.identity.provider_id,
                entry.result.identity.key,
            )
        )
        if self._request is None:
            return ()
        results: list[SearchResult] = []
        seen_keys: set[str] = set()
        for entry in entries:
            key = entry.result.deduplication_key
            if key in seen_keys:
                continue
            seen_keys.add(key)
            results.append(entry.result)
            if len(results) >= self._request.query.limit:
                break
        return tuple(results)

    def _ranked_score(self, result: SearchResult) -> float:
        adjuster = self._rank_adjuster
        request = self._request
        if request is None:
            return result.score
        adjustment = float(adjuster(result, request.query))
        if not math.isfinite(adjustment):
            return result.score
        return result.score + max(
            -RANK_ADJUSTMENT_LIMIT,
            min(RANK_ADJUSTMENT_LIMIT, adjustment),
        )

    def _refresh_selection_unlocked(self) -> None:
        results = self._ranked_results_unlocked()
        identities = {result.identity for result in results}
        target = self._selection_target
        if target is not None and target in identities:
            self._selected_identity = target
            self._waiting_for_initial_selection = False
            return
        if (
            self._waiting_for_initial_selection
            and target is not None
            and target.provider_id in self._pending_provider_ids
        ):
            self._selected_identity = None
            return
        self._selected_identity = results[0].identity if results else None
        self._selection_target = self._selected_identity
        self._waiting_for_initial_selection = False

    def _publish(
        self,
        callback: Callable[[SearchSnapshot], None] | None,
    ) -> None:
        if callback is not None:
            callback(self.snapshot())
