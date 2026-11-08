"""Semantic tray inventory, activation, menus and lifecycle boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping

from docking.platform.backends.base import Rect
from docking.platform.tray.menu import TrayMenuClient
from docking.platform.tray.state import TrayFocusRequest, TrayState


class TrayBackend(ABC):
    @abstractmethod
    def initial_state(self) -> TrayState: ...
    @abstractmethod
    def get_state(self) -> TrayState: ...
    @abstractmethod
    def set_geometry(self, bounds: Mapping[str, Rect]) -> None: ...
    @abstractmethod
    def pointer(
        self,
        identifier: str | None,
        *,
        point: tuple[int, int] | None = None,
        anchor: Rect | None = None,
    ) -> bool: ...
    @abstractmethod
    def set_focus_handler(
        self, handler: Callable[[TrayFocusRequest], None] | None
    ) -> None: ...
    @abstractmethod
    def activate(
        self, identifier: str, *, anchor: Rect | None = None, keyboard: bool = False
    ) -> bool: ...
    @abstractmethod
    def context_menu(
        self, identifier: str, *, anchor: Rect | None = None, keyboard: bool = False
    ) -> bool: ...
    @abstractmethod
    def double_click(self, identifier: str, *, anchor: Rect | None = None) -> bool: ...
    @abstractmethod
    def menu_client(self, identifier: str) -> TrayMenuClient | None: ...
    @abstractmethod
    def close(self) -> None: ...
