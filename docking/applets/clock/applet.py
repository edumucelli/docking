"""GTK lifecycle glue for Clock applet."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib, Gtk

from docking.applets.base import Applet
from docking.applets.clock import meta
from docking.applets.clock.render import render_icon
from docking.applets.clock.state import (
    build_tooltip,
    load_prefs,
    save_payload,
)
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config


class ClockApplet(Applet):
    """Displays current time as an analog clock face or digital readout."""

    id = meta.id
    name = _("Clock")
    icon_name = "clock"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer = _MinuteTimer()

        # Load prefs before the initial presentation sync.
        prefs = config.applet_prefs.get("clock", {}) if config else None
        self._show_digital, self._show_military, self._show_date = load_prefs(
            prefs=prefs
        )

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int):
        """Render clock icon in current mode."""
        now = time.localtime()
        return render_icon(
            size=size,
            now=now,
            show_digital=self._show_digital,
            show_military=self._show_military,
            show_date=self._show_date,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            now=time.localtime(),
            is_24h=self._show_military,
        )

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Three toggles: Digital Clock, 24-Hour Clock, Show Date."""
        items: list[Gtk.MenuItem] = []

        digital = Gtk.CheckMenuItem(label=_("Digital Clock"))
        digital.set_active(self._show_digital)
        digital.connect("toggled", self._on_toggle_digital)
        items.append(digital)

        military = Gtk.CheckMenuItem(label=_("24-Hour Clock"))
        military.set_active(self._show_military)
        military.connect("toggled", self._on_toggle_military)
        items.append(military)

        date = Gtk.CheckMenuItem(label=_("Show Date"))
        date.set_active(self._show_date)
        date.set_sensitive(self._show_digital)
        date.connect("toggled", self._on_toggle_date)
        items.append(date)

        return items

    def _on_toggle_digital(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_digital = widget.get_active()
        self._save_prefs()
        self.present()

    def _on_toggle_military(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_military = widget.get_active()
        self._save_prefs()
        self.present()

    def _on_toggle_date(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_date = widget.get_active()
        self._save_prefs()
        self.present()

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=save_payload(
                show_digital=self._show_digital,
                show_military=self._show_military,
                show_date=self._show_date,
            )
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer.start(callback=self.present)

    def stop(self) -> None:
        self._timer.stop()
        super().stop()


class _MinuteTimer:
    """1-second GLib timer that fires a callback once per minute change."""

    def __init__(self) -> None:
        self._timer_id: int = 0
        self._last_minute: int = -1
        self._callback: Callable[[], None] | None = None

    def start(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self._callback = None

    def _tick(self) -> bool:
        now = time.localtime()
        if now.tm_min != self._last_minute:
            self._last_minute = now.tm_min
            if self._callback:
                self._callback()
        return True
