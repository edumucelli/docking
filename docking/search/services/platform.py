"""Typed Search integration factories injected into the controller."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from .global_shortcuts import ActivationCallback, GlobalShortcutService, StatusCallback


class WindowFocus(ABC):
    @abstractmethod
    def before_present(self) -> None: ...

    @abstractmethod
    def after_present(self, *, title: str, visible: Callable[[], bool]) -> None: ...

    @abstractmethod
    def cancel(self) -> None: ...


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
    @property
    @abstractmethod
    def opaque_action_panel(self) -> bool:
        """Whether the native popup requires an opaque action overlay."""
        ...

    @abstractmethod
    def focus_factory(self) -> Callable[[], WindowFocus] | None: ...
    @abstractmethod
    def primary(
        self,
        *,
        app_id: str,
        preferred_trigger: str,
        on_activated: ActivationCallback,
        on_status_changed: StatusCallback,
        trigger_description: str,
        schedule_idle: Callable[..., int],
        remove_source: Callable[[int], bool],
    ) -> GlobalShortcutService: ...
    @abstractmethod
    def fallback(
        self,
        *,
        shortcut: str,
        on_activated: Callable[[int], None],
        schedule_idle: Callable[..., int],
    ) -> ShortcutFallback | None: ...
