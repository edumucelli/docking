"""Optional application artwork as immutable pixels, independent of GTK."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from docking.platform.applications.types import ApplicationInfo


@dataclass(frozen=True)
class IconPixels:
    """Tightly packed, straight-alpha RGBA pixels ready for presentation."""

    pixels: bytes
    width: int
    height: int


class ApplicationIconSource(Protocol):
    """Nonblocking optional artwork with main-loop readiness notifications."""

    def get(self, info: ApplicationInfo, size: int) -> IconPixels | None: ...

    def stop(self) -> None: ...


def create_application_icon_source(
    on_ready: Callable[[str], None],
) -> ApplicationIconSource | None:
    """Unix uses Gio/theme artwork; Windows supplements it with shell pixels."""
    from docking.platform.environment import is_windows

    if not is_windows():
        return None
    from docking.platform.backends.windows.icons import WindowsIconCache

    return WindowsIconCache(on_ready)
