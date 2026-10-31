"""Typed Search integration factories injected into the controller."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from .global_shortcuts import ActivationCallback, GlobalShortcutService, StatusCallback


class ShortcutFallback(ABC):
    """Controller-facing lifecycle contract for a non-portal shortcut."""

    @property
    @abstractmethod
    def active(self) -> bool:
        """Return whether the fallback currently owns its global grab."""
        ...

    @property
    @abstractmethod
    def error(self) -> str | None:
        """Return the latest startup error, if any."""
        ...

    @abstractmethod
    def start(self) -> bool:
        """Start the fallback and report whether its grab became active."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Release the global grab and invalidate queued activations."""
        ...


class SearchIntegration(ABC):
    @abstractmethod
    def primary(
        self,
        *,
        app_id: str,
        preferred_trigger: str,
        on_activated: ActivationCallback,
        on_status_changed: StatusCallback,
    ) -> GlobalShortcutService: ...
    @abstractmethod
    def fallback(
        self,
        *,
        shortcut: str,
        on_activated: Callable[[int], None],
        schedule_idle: Callable[..., int],
    ) -> ShortcutFallback | None: ...
