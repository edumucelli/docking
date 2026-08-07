"""Compatibility search projection over the canonical application registry."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import suppress
from pathlib import Path

from docking.platform.applications.projections import (
    ApplicationSnapshot,
    DesktopActionSnapshot,
    IconDescriptor,
    normalize_search_text,
    search_applications,
)
from docking.platform.applications.registry import (
    DEFAULT_DEBOUNCE_MS,
    ApplicationRegistry,
)

CatalogListener = Callable[[], None]


class ApplicationCatalog:
    """Expose registry metadata through the historical search-catalog API."""

    def __init__(
        self,
        *,
        registry: ApplicationRegistry | None = None,
        application_source: Callable[[], Iterable[object]] | None = None,
        desktop_directories_source: Callable[[], Iterable[Path]] | None = None,
    ) -> None:
        if registry is not None and (
            application_source is not None or desktop_directories_source is not None
        ):
            raise TypeError("registry sources are only valid in owned mode")
        self._owns_registry = registry is None
        self._registry = (
            registry
            if registry is not None
            else ApplicationRegistry(
                application_source=application_source,
                desktop_directories_source=desktop_directories_source,
            )
        )
        self._registry_unsubscribe: Callable[[], None] | None = None
        self._applications_by_id: dict[str, ApplicationSnapshot] = {}
        self._ordered_snapshot: tuple[ApplicationSnapshot, ...] = ()
        self._listeners: list[CatalogListener] = []
        self._generation = 0
        self._published = False
        self._started = False

    @property
    def registry(self) -> ApplicationRegistry:
        """Return the owned or borrowed canonical registry."""
        return self._registry

    @property
    def generation(self) -> int:
        """Return the version of the published search projection."""
        return self._generation

    @property
    def started(self) -> bool:
        """Return whether registry observation is active."""
        return self._started

    @property
    def applications(self) -> tuple[ApplicationSnapshot, ...]:
        """Return the immutable current search projection."""
        return self._ordered_snapshot

    def snapshot(self) -> tuple[ApplicationSnapshot, ...]:
        """Return the immutable current search projection."""
        return self._ordered_snapshot

    def get(self, desktop_id: str) -> ApplicationSnapshot | None:
        """Return one projected application by desktop ID."""
        return self._applications_by_id.get(desktop_id)

    def add_listener(self, listener: CatalogListener) -> Callable[[], None]:
        """Subscribe once and return an idempotent unsubscribe callback."""
        if listener not in self._listeners:
            self._listeners.append(listener)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if not subscribed:
                return
            subscribed = False
            self.remove_listener(listener)

        return unsubscribe

    def subscribe(self, listener: CatalogListener) -> Callable[[], None]:
        """Alias for :meth:`add_listener`."""
        return self.add_listener(listener)

    def remove_listener(self, listener: CatalogListener) -> None:
        """Remove a previously registered listener."""
        with suppress(ValueError):
            self._listeners.remove(listener)

    def start(self) -> None:
        """Observe the registry, owning its lifecycle only in owned mode."""
        if self._started:
            return
        self._started = True
        self._registry_unsubscribe = self._registry.subscribe(self._on_registry_changed)
        if self._owns_registry:
            self._registry.start()
            if self._registry.generation == 0:
                return
        self._publish_projection()

    def stop(self) -> None:
        """Stop observation while preserving the latest projection."""
        if not self._started:
            return
        self._started = False
        unsubscribe = self._registry_unsubscribe
        self._registry_unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()
        if self._owns_registry:
            self._registry.stop()

    def refresh(self) -> bool:
        """Synchronously refresh the registry, then publish its search projection."""
        generation = self._generation
        self._registry.refresh()
        if self._owns_registry and self._registry.generation == 0:
            return False
        self._publish_projection()
        return self._generation != generation

    def _on_registry_changed(self) -> None:
        if self._started:
            self._publish_projection()

    def _publish_projection(self) -> bool:
        ordered = search_applications(self._registry.snapshot())
        if self._published and ordered == self._ordered_snapshot:
            return False
        self._published = True
        self._ordered_snapshot = ordered
        self._applications_by_id = {
            application.desktop_id: application for application in ordered
        }
        self._generation += 1
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                continue
        return True


__all__ = [
    "DEFAULT_DEBOUNCE_MS",
    "ApplicationCatalog",
    "ApplicationSnapshot",
    "CatalogListener",
    "DesktopActionSnapshot",
    "IconDescriptor",
    "normalize_search_text",
]
