"""Unix desktop adapter with lazy GUI operation imports."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from docking.platform.desktop.base import DesktopServices

from ...base import IconLocations

if TYPE_CHECKING:
    from gi.repository import Gio

    from docking.platform.icon_source import ApplicationIconSource


class UnixDesktopServices(DesktopServices):
    def compositor_active(self, *, display: object | None = None) -> bool | None:
        from .diagnostics import compositor_active

        return compositor_active(display=display)

    def text_modifier_active(self) -> bool:
        return False

    def create_icon_source(
        self, on_ready: Callable[[str], None]
    ) -> ApplicationIconSource | None:
        # Unix uses theme/Gio artwork and retains no native readiness subscription.
        del on_ready
        return None

    def icon_locations(self) -> IconLocations:
        from docking.platform.host.implementations.unix.paths import (
            HOST_FILESYSTEM_ROOT,
            HOST_XDG_DATA_DIRS,
        )

        return IconLocations(HOST_XDG_DATA_DIRS, HOST_FILESYSTEM_ROOT)

    def file_for_uri(self, uri: str) -> Gio.File:
        from .files import file_for_uri

        return file_for_uri(uri)

    def file_for_path(self, path: str) -> Gio.File:
        from .files import file_for_path

        return file_for_path(path)

    def open_uri(self, uri: str) -> bool:
        from .targets import open_uri

        return open_uri(uri)

    def prepare_display(self) -> bool:
        from docking.platform.backends.cinnamon.startup import prepare_cinnamon_display

        return prepare_cinnamon_display()

    def install_quit_handlers(self, quit: Callable[[], bool]) -> Callable[[], None]:
        from .lifecycle import install_quit_handlers

        return install_quit_handlers(quit)
