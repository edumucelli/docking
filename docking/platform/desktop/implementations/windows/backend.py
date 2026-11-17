"""Windows desktop operations with lazy GTK and native-resource loading."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from docking.platform.desktop.base import (
    DesktopServices,
    IconLocations,
    OpenWithMode,
    OpenWithResult,
)

if TYPE_CHECKING:
    from gi.repository import Gio

    from docking.platform.desktop.base import ApplicationIconSource


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

    def open_in_terminal(self, path: Path) -> bool:
        from .file_actions import open_in_terminal

        return open_in_terminal(path)

    def open_with_mode(self, *, is_dir: bool) -> OpenWithMode | None:
        return None if is_dir else OpenWithMode.NATIVE

    def request_open_with(
        self,
        uri: str,
        *,
        parent: object,
        on_result: Callable[[OpenWithResult], None],
    ) -> Callable[[], None]:
        from .file_actions import request_open_with

        return request_open_with(uri, parent=parent, on_result=on_result)

    def prepare_display(self) -> bool:
        from docking.platform.native.windows.startup import prepare_windows_runtime

        return prepare_windows_runtime()

    def install_quit_handlers(self, quit: Callable[[], bool]) -> Callable[[], None]:
        from .lifecycle import install_quit_handlers

        return install_quit_handlers(quit)

    def text_modifier_active(self) -> bool:
        from docking.platform.native.windows.modifiers import altgr_pressed

        return altgr_pressed()

    def create_icon_source(
        self, on_ready: Callable[[str], None]
    ) -> ApplicationIconSource:
        from .icons import WindowsIconCache

        return WindowsIconCache(on_ready)
