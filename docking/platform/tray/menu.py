"""Neutral tray menu values and actions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DBusMenuItem:
    """One menu item from a DBusMenu layout tree."""

    item_id: int
    label: str
    enabled: bool = True
    visible: bool = True
    item_type: str = ""
    icon_name: str = ""
    icon_data: bytes = b""
    toggle_type: str = ""
    toggle_state: int = -1
    children: tuple[DBusMenuItem, ...] = ()

    @property
    def is_separator(self) -> bool:
        return self.item_type == "separator"


@dataclass(frozen=True, slots=True)
class DBusMenuLayout:
    """Parsed DBusMenu layout."""

    revision: int
    root: DBusMenuItem


class TrayMenuClient(ABC):
    @abstractmethod
    def get_layout(self) -> DBusMenuLayout | None: ...
    @abstractmethod
    def about_to_show(self, item_id: int) -> bool: ...
    @abstractmethod
    def event(self, item_id: int, event_id: str = "clicked") -> bool: ...
