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

"""GTK lifecycle glue for Battery applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.battery import meta
from docking.applets.battery.render import render_icon
from docking.applets.battery.state import (
    BatteryState,
    open_power_settings,
    power_settings_command,
    read_battery,
    tooltip_text,
)
from docking.applets.menu import menu_sections
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config


class BatteryApplet(Applet):
    """Shows battery charge icon from sysfs, polled every 60 seconds."""

    id = meta.id
    name = _("Battery")
    icon_name = "battery-good"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._show_percent = False
        if config:
            prefs = config.applet_prefs.get(meta.id, {})
            self._show_percent = bool(prefs.get("show_percent", False))
        self._state: BatteryState | None = read_battery()
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load battery theme icon matching current state."""
        return render_icon(
            size=size,
            state=self._state,
            show_percent=self._show_percent,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(state=self._state)

    def start(self, notify: Callable[[], None]) -> None:
        """Start 60-second polling timer (battery changes slowly)."""
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(60, self._tick)

    def stop(self) -> None:
        """Stop the polling timer."""
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        show = Gtk.CheckMenuItem(label=_("Show Percent"))
        show.set_active(self._show_percent)
        show.connect("toggled", self._on_toggle_percent)

        settings: list[Gtk.MenuItem] = []
        cmd = power_settings_command()
        if cmd is not None:
            power_settings = Gtk.MenuItem(label=_("Power Settings"))
            power_settings.connect("activate", lambda _widget: open_power_settings())
            settings.append(power_settings)
        return menu_sections(display=[show], settings=settings, gtk=Gtk)

    def _on_toggle_percent(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_percent = widget.get_active()
        self.save_prefs(prefs={"show_percent": self._show_percent})
        self.present()

    def _tick(self) -> bool:
        """Re-read sysfs and refresh icon."""
        self._state = read_battery()
        self.present()
        return True
