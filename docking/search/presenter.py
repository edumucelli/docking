"""Define the minimal search presentation surface used outside the package.

Applets and runtime integrations only need to show, hide, or toggle the shared
palette. This protocol prevents those callers from depending on the concrete
GTK window, provider graph, shortcut services, or controller lifecycle. It
also makes a no-op or test presenter straightforward to supply.
"""

from __future__ import annotations

from typing import Protocol


class SearchPresenter(Protocol):
    """Commands exposed to applets and other non-UI callers."""

    def show(
        self,
        initial_query: str = "",
        activation_context: dict[str, object] | None = None,
    ) -> None:
        """Present the palette with optional initial text and platform context."""
        ...

    def hide(self) -> None:
        """Hide the palette if it is visible."""
        ...

    def toggle(
        self,
        activation_context: dict[str, object] | None = None,
    ) -> None:
        """Toggle visibility while preserving any platform activation context."""
        ...

    @property
    def visible(self) -> bool:
        """Return whether the shared palette is visible."""
        ...


__all__ = ["SearchPresenter"]
