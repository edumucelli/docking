"""Transport-independent tray menu trees and explicit menu operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrayMenuItem:
    item_id: int
    label: str
    enabled: bool = True
    visible: bool = True
    item_type: str = ""
    icon_name: str = ""
    icon_data: bytes = b""
    toggle_type: str = ""
    toggle_state: int = -1
    children: tuple[TrayMenuItem, ...] = ()

    @property
    def is_separator(self) -> bool:
        return self.item_type == "separator"


@dataclass(frozen=True, slots=True)
class TrayMenuLayout:
    revision: int
    root: TrayMenuItem


class TrayMenuClient(ABC):
    @abstractmethod
    def about_to_show(self, item_id: int = 0) -> bool: ...
    @abstractmethod
    def get_layout(self) -> TrayMenuLayout | None: ...
    @abstractmethod
    def event(self, item_id: int) -> bool: ...
