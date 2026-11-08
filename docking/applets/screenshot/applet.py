# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Screenshot applet behavior and GTK wiring."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

import cairo
import gi

from docking.applets.platform import AppletPlatformServices

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.base import PlatformServicesApplet
from docking.applets.menu import menu_sections
from docking.applets.popup import capture_source_window
from docking.applets.screenshot import meta
from docking.applets.worker import BackgroundWorker
from docking.core.icons import IconSource
from docking.i18n import _
from docking.log import get_logger, with_context
from docking.platform.backends.base import Rect

from .preview import ScreenshotPreview
from .render import _draw_screenshot_icon
from .selection import RegionSelection
from .state import Mode, ScreenshotResult, SelectedRegion

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="screenshot"), applet_id=meta.id)
_TIMED_DELAYS_S: tuple[int, ...] = (3, 5, 7, 9)
_MODE_OPTIONS: tuple[tuple[str, Mode], ...] = (
    ("Full Screen", "full"),
    ("Window", "window"),
    ("Region", "region"),
)


class ScreenshotApplet(PlatformServicesApplet):
    """Capture screenshots via the best available tool."""

    id = meta.id
    name = _("Screenshot")
    icon_name = "applets-screenshooter"
    icon_source_options = (IconSource.DOCKING, IconSource.SYSTEM)

    def __init__(
        self,
        icon_size: int,
        config: Config,
        *,
        platform_services: AppletPlatformServices,
    ) -> None:
        self._platform = platform_services
        self._backend = self._platform.backends.screenshot()
        # Native capture resources must finish closing even after GTK exits.
        self._worker = BackgroundWorker(
            thread_factory=lambda target, daemon: threading.Thread(
                target=target, daemon=False, name="screenshot-worker"
            )
        )
        self._available = False
        self._loading = True
        self._pending = False
        self._error = False
        self._stopped = False
        self._generation = 0
        self._delay_source = 0
        self._selection: RegionSelection | None = None
        self._preview: ScreenshotPreview | None = None
        super().__init__(icon_size, config, platform_services=platform_services)
        self.present()
        self._discover()

    def _discover(self) -> None:
        generation = self._generation
        backend = self._backend
        self._worker.run(
            name="screenshot-discover",
            fn=backend.available,
            on_result=lambda available: self._discovered(available, generation),
            on_error=lambda _error: self._discovered(False, generation),
        )

    def _discovered(self, available: bool, generation: int) -> bool:
        if not self._stopped and generation == self._generation:
            self._available, self._loading = available, False
            self.present()
        return False

    def start(self, notify: Callable[[], None]) -> None:
        if self._stopped:
            self._generation += 1
            self._stopped = self._pending = self._error = False
            self._available, self._loading = False, True
            self._backend = self._platform.backends.screenshot()
            self._discover()
        super().start(notify)

    def refresh_tooltip(self) -> None:
        self.item.name = self.name
        if self._loading or self._pending:
            self.item.name += "\n" + _("Loading...")
        elif not self._available or self._error:
            self.item.name += "\n" + _("Unavailable")

    def stop(self) -> None:
        self._stopped = True
        self._generation += 1
        if self._delay_source:
            GLib.source_remove(self._delay_source)
            self._delay_source = 0
        if self._selection is not None:
            self._selection.close()
            self._selection = None
        if self._preview is not None:
            self._preview.close()
            self._preview = None
        self._backend.stop()
        super().stop()

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _draw_screenshot_icon(cr=cr, size=size)
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def on_clicked(self) -> None:
        """Full-screen capture on left-click."""
        if not self._available or self._stopped:
            return
        self._run_mode(mode="full")

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        if not self._available or self._stopped:
            return []
        primary: list[Gtk.MenuItem] = []
        for label, mode in _MODE_OPTIONS:
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda _w, m=mode: self._run_mode(mode=m))
            primary.append(mi)

        manage: list[Gtk.MenuItem] = []
        for delay_s in _TIMED_DELAYS_S:
            mi = Gtk.MenuItem(label=_("Full Screen in {delay}s").format(delay=delay_s))
            mi.connect(
                "activate",
                lambda _w, d=delay_s: self._run_mode(mode="full", delay_seconds=d),
            )
            manage.append(mi)
        return menu_sections(primary=primary, manage=manage, gtk=Gtk)

    def _run_mode(self, *, mode: Mode, delay_seconds: int = 0) -> None:
        if not self._available or self._stopped:
            return
        if self._pending or self._delay_source:
            return
        anchor = self.popup_anchor
        self._backend.prepare_capture(
            anchor.parent if anchor is not None else None,
            source=capture_source_window(anchor) if mode == "window" else None,
        )
        self._error = False
        if mode == "region" and self._backend.select_region:
            if self._selection is None or self._selection.window is None:
                self._selection = RegionSelection(
                    self._region_selected,
                    anchor=self.popup_anchor,
                    backend=self._backend,
                )
            return
        self._queue(mode, delay_seconds=delay_seconds)

    def _region_selected(self, region: SelectedRegion) -> None:
        if self._stopped:
            return
        self._selection = None
        try:
            prepared = self._backend.prepare_region(region)
        except OSError as exc:
            log.debug("Could not map screenshot region: %s", exc)
            prepared = None
        if prepared is None:
            self._error = True
            self.present()
            return
        self._queue("region", region=prepared)

    def _queue(
        self, mode: Mode, *, delay_seconds: int = 0, region: Rect | None = None
    ) -> None:
        self._generation += 1
        generation = self._generation
        backend = self._backend
        self._pending = True
        self.present()

        def capture() -> bool:
            self._delay_source = 0
            if not self._stopped and generation == self._generation:
                self._worker.run(
                    name="screenshot-capture",
                    fn=lambda: (
                        backend.capture(mode, region=region)
                        if not self._stopped and generation == self._generation
                        else ScreenshotResult(False)
                    ),
                    on_result=lambda result: self._captured(result, generation),
                    on_error=lambda _error: self._captured(
                        ScreenshotResult(False), generation
                    ),
                )
            return False

        # Close the applet menu or input overlay before reading desktop pixels.
        self._delay_source = GLib.timeout_add(max(150, delay_seconds * 1000), capture)

    def _captured(self, result: ScreenshotResult, generation: int) -> bool:
        if self._stopped or generation != self._generation:
            return False
        self._pending = False
        self._error = not result.accepted
        if result.image is not None:
            if self._preview is not None:
                self._preview.close()
            self._preview = ScreenshotPreview(result.image, anchor=self.popup_anchor)
        self.present()
        return False
