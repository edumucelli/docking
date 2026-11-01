"""Optional platform presentation extensions for the shared tray applet."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gi.repository import Gtk


class TrayPresentation(ABC):
    @property
    @abstractmethod
    def active(self) -> bool: ...
    @property
    @abstractmethod
    def count(self) -> int: ...
    @abstractmethod
    def tooltip(self) -> str: ...
    @abstractmethod
    def clicked(self) -> bool: ...
    @abstractmethod
    def menu_items(self) -> list[Gtk.MenuItem]: ...
    @abstractmethod
    def position(self) -> None: ...
    @abstractmethod
    def empty_message(self) -> str: ...
    @abstractmethod
    def stop(self) -> None: ...
