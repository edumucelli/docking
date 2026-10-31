"""Optional platform presentation extensions for the shared tray applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from gi.repository import Gtk

from docking.applets.popup import PopupAnchor
from docking.platform import environment
from docking.platform.tray.state import TrayState


class TrayPresentation(Protocol):
    @property
    def active(self) -> bool: ...
    @property
    def count(self) -> int: ...
    def tooltip(self) -> str: ...
    def clicked(self) -> bool: ...
    def menu_items(self) -> list[Gtk.MenuItem]: ...
    def position(self) -> None: ...
    def empty_message(self) -> str: ...
    def stop(self) -> None: ...


def create_platform_presentation(
    *,
    icon_size: int,
    changed: Callable[[], None],
    state: Callable[[], TrayState],
    anchor: Callable[[], PopupAnchor | None],
    refresh: Callable[[], None],
) -> TrayPresentation | None:
    if environment.is_windows():
        return None
    from .unix_presentation import UnixTrayPresentation

    return UnixTrayPresentation(
        icon_size=icon_size,
        changed=changed,
        state=state,
        anchor=anchor,
        refresh=refresh,
    )
