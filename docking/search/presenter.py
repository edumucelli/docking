"""Narrow presentation contract for opening the global search palette."""

from __future__ import annotations

from typing import Protocol


class SearchPresenter(Protocol):
    """Commands exposed to applets and other non-UI callers."""

    def show(
        self,
        initial_query: str = "",
        activation_context: dict[str, object] | None = None,
    ) -> None: ...

    def hide(self) -> None: ...

    def toggle(
        self,
        activation_context: dict[str, object] | None = None,
    ) -> None: ...

    @property
    def visible(self) -> bool: ...


__all__ = ["SearchPresenter"]
