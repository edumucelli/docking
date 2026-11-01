"""Windows desktop operations with lazy GTK and native-resource loading."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from docking.platform.desktop.base import DesktopServices, IconLocations

if TYPE_CHECKING:
    from gi.repository import Gio

    from docking.platform.icon_source import ApplicationIconSource


class WindowsDesktopServices(DesktopServices):
    def compositor_active(self, *, display: object | None = None) -> bool | None:
        # This port publishes no X11 compositor measurement on Windows.
        return None

    def icon_locations(self) -> IconLocations:
        return IconLocations((), None)

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
        from docking.platform.native.windows.startup import prepare_windows_runtime

        return prepare_windows_runtime()

    def install_quit_handlers(self, quit: Callable[[], bool]) -> Callable[[], None]:
        from .lifecycle import install_quit_handlers

        return install_quit_handlers(quit)

    def text_modifier_active(self) -> bool:
        from .keyboard import text_modifier_active

        return text_modifier_active()

    def create_icon_source(
        self, on_ready: Callable[[str], None]
    ) -> ApplicationIconSource:
        from .icons import WindowsIconCache

        return WindowsIconCache(on_ready)
