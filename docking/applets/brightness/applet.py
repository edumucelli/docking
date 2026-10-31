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

"""GTK lifecycle for Brightness applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

from docking.applets.brightness.backend import BrightnessOperations
from docking.applets.platform import AppletPlatformServices

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import PlatformServicesApplet
from docking.applets.brightness import meta
from docking.applets.brightness.render import create_icon
from docking.applets.brightness.state import STEP
from docking.applets.menu import menu_sections
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="brightness"), applet_id=meta.id)

POLL_INTERVAL_S = 5


class BrightnessApplet(PlatformServicesApplet):
    """Screen brightness control via xrandr.

    Scroll adjusts brightness ±5%. Click resets to 100%.
    Auto-detects primary xrandr output.
    """

    id = meta.id
    name = _("Brightness")
    icon_name = "display-brightness-symbolic"

    def __init__(
        self,
        icon_size: int,
        config: Config,
        *,
        platform_services: AppletPlatformServices,
    ) -> None:
        self._platform = platform_services
        self._native = platform_services.backends.brightness()
        self._backend = self._native if self._native.available() else None
        if not self._backend:
            log.warning("No xrandr output detected")
        self._brightness = 1.0
        self._show_level = False
        self._timer_id: int = 0
        self._set_in_progress = False
        self._pending_brightness: float | None = None
        self._poll_generation = 0
        self._worker = BackgroundWorker(logger=log)

        prefs = config.applet_prefs.get(meta.id, {})
        self._show_level = prefs.get("show_level", False)

        self._poll()
        super().__init__(
            icon_size=icon_size, config=config, platform_services=platform_services
        )
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(
            size=size,
            brightness=self._brightness,
            show_level=self._show_level,
        )

    def refresh_tooltip(self) -> None:
        # The backend rounds before applying, so truncating here showed one
        # percent less than the hardware got.
        pct = round(self._brightness * 100)
        self.item.name = _("Brightness: {pct}%").format(pct=pct)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        self._poll_generation += 1
        self._pending_brightness = None
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        """Reset to 100% on click."""
        if self._backend:
            self._brightness = 1.0
            self.present()
            self._queue_brightness(1.0)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        show = Gtk.CheckMenuItem(label=_("Show Level"))
        show.set_active(self._show_level)
        show.connect("toggled", self._on_toggle_level)
        return menu_sections(display=[show], gtk=Gtk)

    def _on_toggle_level(self, widget) -> None:
        self._show_level = widget.get_active()
        self.save_prefs(prefs={"show_level": self._show_level})
        self.present()

    def on_scroll(self, direction_up: bool) -> None:
        """Adjust brightness +-2% on scroll."""
        if not self._backend:
            return
        if direction_up:
            new = min(1.0, self._brightness + STEP)
        else:
            new = max(0.1, self._brightness - STEP)
        self._brightness = new
        self.present()
        self._queue_brightness(new)

    def _queue_brightness(self, value: float) -> None:
        self._poll_generation += 1
        self._pending_brightness = value
        self._start_pending_brightness()

    def _start_pending_brightness(self) -> None:
        if self._set_in_progress or self._pending_brightness is None:
            return
        backend, value = self._backend, self._pending_brightness
        if backend is None:
            return
        self._pending_brightness = None
        self._set_in_progress = True

        self._worker.run(
            name="brightness-set",
            fn=lambda: self._write_brightness(backend, value),
            on_result=self._on_brightness_set,
            on_error=lambda _error: self._on_brightness_set(),
        )

    def _on_brightness_set(self, actual: float | None = None) -> bool:
        self._set_in_progress = False
        if actual is not None and self._pending_brightness is None:
            self._brightness = actual
            self.present()
        self._start_pending_brightness()
        return False

    def _write_brightness(
        self, backend: BrightnessOperations, value: float
    ) -> float | None:
        if backend.set_brightness(value=value) is False:
            return backend.get_brightness()
        return None

    def _poll(self) -> None:
        """Read current brightness synchronously."""
        if not self._backend:
            return
        val = self._backend.get_brightness()
        if val is not None:
            self._brightness = val

    def _tick(self) -> bool:
        """Periodic poll in background thread."""
        backend = self._backend
        if not backend or self._set_in_progress:
            return True
        generation = self._poll_generation
        self._worker.run(
            name="brightness-poll",
            fn=lambda: backend.get_brightness(),
            on_result=lambda value: self._on_poll_result(value, generation=generation),
        )
        return True

    def _on_poll_result(
        self, val: float | None, *, generation: int | None = None
    ) -> bool:
        if self._set_in_progress or (
            generation is not None and generation != self._poll_generation
        ):
            return False
        if val is not None and abs(val - self._brightness) > 0.01:
            self._brightness = val
            self.present()
        return False
