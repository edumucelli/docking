"""Legacy tray presentation independent of its native host."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.core.position import Position


@dataclass(frozen=True, slots=True)
class LegacyTrayIcon:
    """One XEmbed tray icon currently docked into Docking."""

    xid: int
    title: str


class TrayPresentation(ABC):
    @property
    @abstractmethod
    def active(self) -> bool: ...

    @property
    @abstractmethod
    def unavailable_reason(self) -> str: ...

    @property
    @abstractmethod
    def visible(self) -> bool: ...

    @property
    @abstractmethod
    def icons(self) -> tuple[LegacyTrayIcon, ...]: ...

    @abstractmethod
    def start(
        self,
        *,
        force: bool = False,
        anchor_x: int = 0,
        anchor_y: int = 0,
        position: Position | None = None,
    ) -> bool: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def position_near(
        self, *, anchor_x: int = 0, anchor_y: int = 0, position: Position | None = None
    ) -> None: ...

    @abstractmethod
    def show(self) -> None: ...

    @abstractmethod
    def hide(self) -> None: ...

    @abstractmethod
    def toggle_visible(
        self, *, anchor_x: int = 0, anchor_y: int = 0, position: Position | None = None
    ) -> bool: ...
