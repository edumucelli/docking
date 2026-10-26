"""Semantic tray inventory, activation, menus and lifecycle boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from docking.platform import environment
from docking.platform.backends.base import Rect
from docking.platform.tray.menu import TrayMenuClient
from docking.platform.tray.state import TrayState


class TrayBackend(Protocol):
    def get_state(self) -> TrayState: ...
    def set_geometry(self, bounds: Mapping[str, Rect]) -> None: ...
    def activate(self, identifier: str, *, anchor: Rect | None = None) -> bool: ...
    def context_menu(self, identifier: str, *, anchor: Rect | None = None) -> bool: ...
    def double_click(self, identifier: str, *, anchor: Rect | None = None) -> bool: ...
    def menu_client(self, identifier: str) -> TrayMenuClient | None: ...
    def close(self) -> None: ...


def create_backend() -> TrayBackend:
    if environment.is_windows():
        from docking.platform.tray.windows import WindowsTrayBackend

        return WindowsTrayBackend()
    from docking.platform.tray.linux import LinuxTrayBackend

    return LinuxTrayBackend()
