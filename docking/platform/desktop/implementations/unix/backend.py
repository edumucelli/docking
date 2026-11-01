"""Unix desktop adapter with lazy GUI operation imports."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from docking.log import get_logger
from docking.platform.desktop.base import DesktopServices

from ...base import IconLocations

if TYPE_CHECKING:
    from gi.repository import Gio

    from docking.platform.desktop.base import ApplicationIconSource


log = get_logger("environment")


class UnixDesktopServices(DesktopServices):
    def compositor_active(self, *, display: object | None = None) -> bool | None:
        """Return compositor status for X11 backends, or None when unavailable."""
        from docking.platform.environment import is_x11_backend
        from docking.platform.libraries import load_library

        try:
            import ctypes

            if display is None:
                import gi

                gi.require_version("GdkX11", "3.0")
                from gi.repository import GdkX11

                display = GdkX11.X11Display.get_default()
            if display is None or not is_x11_backend(display=display):
                return None

            xlib = load_library("X11", "libX11.so.6")
            xdisplay = ctypes.c_void_p(hash(display.get_xdisplay()))

            xlib.XDefaultScreen.restype = ctypes.c_int
            xlib.XDefaultScreen.argtypes = [ctypes.c_void_p]
            xlib.XInternAtom.restype = ctypes.c_ulong
            xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            xlib.XGetSelectionOwner.restype = ctypes.c_ulong
            xlib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

            screen_num = xlib.XDefaultScreen(xdisplay)
            atom = xlib.XInternAtom(xdisplay, f"_NET_WM_CM_S{screen_num}".encode(), 0)
            owner = xlib.XGetSelectionOwner(xdisplay, atom)
            return bool(owner != 0)
        except Exception as exc:
            log.warning("failed to check compositor status: %s", exc)
            return None

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
