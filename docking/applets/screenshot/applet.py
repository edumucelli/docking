"""Screenshot applet behavior and GTK wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.log import get_logger, with_context

from .render import _draw_screenshot_icon
from .state import _TOOLS, Mode, _detect_tool, _run

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="screenshot"), applet_id=str(AppletId.SCREENSHOT))
_TIMED_DELAYS_S: tuple[int, ...] = (3, 5, 7, 9)


class ScreenshotApplet(Applet):
    """Capture screenshots via the best available tool."""

    id = AppletId.SCREENSHOT
    name = "Screenshot"
    icon_name = "applets-screenshooter"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._tool = _detect_tool()
        if not self._tool:
            _log.bind(action="detect_tool").warning(
                f"No screenshot tool found ({', '.join(t.command for t in _TOOLS)})"
            )
        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _draw_screenshot_icon(cr=cr, size=size)
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def on_clicked(self) -> None:
        """Full-screen capture on left-click."""
        if not self._tool:
            return
        self._run_mode(mode="full")

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []
        tool = self._tool
        if not tool:
            return items
        for label, mode in [
            ("Full Screen", "full"),
            ("Window", "window"),
            ("Region", "region"),
        ]:
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda _w, m=mode: self._run_mode(mode=m))
            items.append(mi)

        items.append(Gtk.SeparatorMenuItem())
        for delay_s in _TIMED_DELAYS_S:
            mi = Gtk.MenuItem(label=f"Full Screen in {delay_s}s")
            mi.connect(
                "activate",
                lambda _w, d=delay_s: self._run_mode(mode="full", delay_seconds=d),
            )
            items.append(mi)
        return items

    def _run_mode(self, *, mode: Mode, delay_seconds: int = 0) -> None:
        if not self._tool:
            return
        try:
            _run(tool=self._tool, mode=mode, delay_seconds=delay_seconds)
        except OSError as exc:
            _log.bind(action=f"screenshot_{mode}").warning(
                "Failed to run screenshot command: %s",
                exc,
            )
