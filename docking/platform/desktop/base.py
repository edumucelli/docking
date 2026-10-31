"""Narrow desktop operations independent of concrete native transports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gi.repository import Gio


@dataclass(frozen=True, slots=True)
class IconLocations:
    host_data_dirs: tuple[str, ...]
    host_filesystem_root: Path


class DesktopServices(ABC):
    @abstractmethod
    def icon_locations(self) -> IconLocations: ...
    @abstractmethod
    def file_for_uri(self, uri: str) -> Gio.File: ...
    @abstractmethod
    def file_for_path(self, path: str) -> Gio.File: ...
    @abstractmethod
    def open_uri(self, uri: str) -> bool: ...
    @abstractmethod
    def prepare_display(self) -> bool: ...
    @abstractmethod
    def install_quit_handlers(self, quit: Callable[[], bool]) -> Callable[[], None]: ...
