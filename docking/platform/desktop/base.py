"""Narrow desktop operations independent of concrete native transports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gi.repository import Gio

    from docking.platform.applications.types import ApplicationInfo


@dataclass(frozen=True)
class IconPixels:
    """Tightly packed, straight-alpha RGBA pixels ready for presentation."""

    pixels: bytes
    width: int
    height: int


class ApplicationIconSource(ABC):
    """Nonblocking optional artwork with main-loop readiness notifications."""

    @abstractmethod
    def get(self, info: ApplicationInfo, size: int) -> IconPixels | None: ...

    @abstractmethod
    def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class IconLocations:
    host_data_dirs: tuple[str, ...]
    host_filesystem_root: Path | None


class OpenWithMode(Enum):
    GTK = "gtk"
    PORTAL = "portal"
    NATIVE = "native"


class OpenWithResult(Enum):
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class LoginStartup(ABC):
    @abstractmethod
    def enabled(self) -> bool: ...

    @abstractmethod
    def set_enabled(self, enabled: bool) -> None: ...


class DesktopServices(ABC):
    @abstractmethod
    def compositor_active(self, *, display: object | None = None) -> bool | None: ...
    @abstractmethod
    def icon_locations(self) -> IconLocations: ...
    @abstractmethod
    def file_for_uri(self, uri: str) -> Gio.File: ...
    @abstractmethod
    def file_for_path(self, path: str) -> Gio.File: ...
    @abstractmethod
    def open_uri(self, uri: str) -> bool: ...
    @abstractmethod
    def open_in_terminal(self, path: Path) -> bool: ...
    @abstractmethod
    def open_with_mode(self, *, is_dir: bool) -> OpenWithMode | None: ...
    @abstractmethod
    def request_open_with(
        self,
        uri: str,
        *,
        parent: object,
        on_result: Callable[[OpenWithResult], None],
    ) -> Callable[[], None]: ...
    @abstractmethod
    def prepare_display(self) -> bool: ...
    @abstractmethod
    def install_quit_handlers(self, quit: Callable[[], bool]) -> Callable[[], None]: ...
    @abstractmethod
    def login_startup(self) -> LoginStartup | None: ...
    @abstractmethod
    def text_modifier_active(self) -> bool: ...
    @abstractmethod
    def create_icon_source(
        self, on_ready: Callable[[str], None]
    ) -> ApplicationIconSource | None: ...
