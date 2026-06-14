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

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.brightness import meta
from docking.applets.brightness.render import create_icon
from docking.applets.brightness.state import (
    STEP,
    detect_output,
    get_brightness,
    set_brightness,
)
from docking.applets.menu import menu_sections
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="brightness"), applet_id=meta.id)

POLL_INTERVAL_S = 5


class BrightnessApplet(Applet):
    """Screen brightness control via xrandr.

    Scroll adjusts brightness ±5%. Click resets to 100%.
    Auto-detects primary xrandr output.
    """

    id = meta.id
    name = _("Brightness")
    icon_name = "display-brightness-symbolic"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._backend = detect_output()
        if not self._backend:
            log.warning("No xrandr output detected")
        self._brightness = 1.0
        self._show_level = False
        self._timer_id: int = 0
        self._worker = BackgroundWorker(logger=log)

        if config:
            prefs = config.applet_prefs.get(meta.id, {})
            self._show_level = prefs.get("show_level", False)

        self._poll()
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(
            size=size,
            brightness=self._brightness,
            show_level=self._show_level,
        )

    def refresh_tooltip(self) -> None:
        pct = int(self._brightness * 100)
        self.item.name = _("Brightness: {pct}%").format(pct=pct)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        """Reset to 100% on click."""
        if self._backend:
            self._brightness = 1.0
            self.present()
            backend = self._backend
            self._worker.run(
                name="brightness-reset",
                fn=lambda: set_brightness(backend=backend, value=1.0),
            )

    def get_menu_items(self) -> list:
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
        backend, value = self._backend, new
        self._worker.run_guarded(
            key="brightness-set",
            name="brightness-scroll",
            fn=lambda: set_brightness(backend=backend, value=value),
        )

    def _poll(self) -> None:
        """Read current brightness synchronously."""
        if not self._backend:
            return
        val = get_brightness(backend=self._backend)
        if val is not None:
            self._brightness = val

    def _tick(self) -> bool:
        """Periodic poll in background thread."""
        backend = self._backend
        if not backend:
            return True
        self._worker.run(
            name="brightness-poll",
            fn=lambda: get_brightness(backend=backend),
            on_result=self._on_poll_result,
        )
        return True

    def _on_poll_result(self, val: float | None) -> bool:
        if val is not None and abs(val - self._brightness) > 0.01:
            self._brightness = val
            self.present()
        return False
