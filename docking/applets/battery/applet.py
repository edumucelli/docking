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
from docking.applets.battery.peripherals import (
    PeripheralBattery,
    read_peripheral_batteries,
    tooltip_lines,
)
from docking.applets.battery.render import render_icon
from docking.applets.battery.state import (
    OVERLAY_NONE,
    OVERLAY_PERCENT,
    OVERLAY_POWER,
    BatteryState,
    open_power_settings,
    overlay_from_prefs,
    power_settings_command,
    read_battery,
    read_power_watts,
    tooltip_text,
)
from docking.applets.menu import menu_sections, radio_menu_items
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config

# Battery charge/status changes slowly; power draw changes by the second, so the
# live power overlay refreshes on a separate, faster sysfs-only timer.
_FULL_POLL_SECONDS = 60
_POWER_POLL_SECONDS = 5


class BatteryApplet(Applet):
    """Shows a battery icon from sysfs, with an optional percent/power overlay."""

    id = meta.id
    name = _("Battery")
    icon_name = "battery-good"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._timer_id: int = 0
        self._power_timer_id: int = 0
        self._overlay = OVERLAY_NONE
        prefs = config.applet_prefs.get(meta.id, {})
        self._overlay = overlay_from_prefs(prefs)
        self._state: BatteryState | None = read_battery()
        self._peripherals: list[PeripheralBattery] = read_peripheral_batteries()
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load battery theme icon matching current state."""
        return render_icon(size=size, state=self._state, overlay=self._overlay)

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_lines(
            battery_line=tooltip_text(state=self._state),
            peripherals=self._peripherals,
        )

    def start(self, notify: Callable[[], None]) -> None:
        """Start the slow full-state poll plus the power overlay poll if needed."""
        super().start(notify=notify)
        self._peripherals = read_peripheral_batteries()
        self._timer_id = GLib.timeout_add_seconds(_FULL_POLL_SECONDS, self._tick)
        self._sync_power_timer()
        self.present()

    def stop(self) -> None:
        """Stop the polling timers."""
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._power_timer_id:
            GLib.source_remove(self._power_timer_id)
            self._power_timer_id = 0
        super().stop()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        overlay = radio_menu_items(
            choices=(
                (_("No overlay"), OVERLAY_NONE),
                (_("Percentage"), OVERLAY_PERCENT),
                (_("Power (W)"), OVERLAY_POWER),
            ),
            active_value=self._overlay,
            on_selected=lambda _widget, value: self._set_overlay(mode=value),
            gtk=Gtk,
        )

        settings: list[Gtk.MenuItem] = []
        cmd = power_settings_command()
        if cmd is not None:
            power_settings = Gtk.MenuItem(label=_("Power Settings"))
            power_settings.connect("activate", lambda _widget: open_power_settings())
            settings.append(power_settings)
        return menu_sections(display=overlay, settings=settings, gtk=Gtk)

    def _set_overlay(self, mode: str) -> None:
        self._overlay = mode
        self.save_prefs(prefs={"overlay": mode})
        self._sync_power_timer()
        self.present()

    def _sync_power_timer(self) -> None:
        """Run the fast power poll only while the power overlay is selected."""
        if self._notify is None:
            return
        if self._overlay == OVERLAY_POWER and not self._power_timer_id:
            self._power_timer_id = GLib.timeout_add_seconds(
                _POWER_POLL_SECONDS, self._power_tick
            )
        elif self._overlay != OVERLAY_POWER and self._power_timer_id:
            GLib.source_remove(self._power_timer_id)
            self._power_timer_id = 0

    def _tick(self) -> bool:
        """Re-read sysfs + peripherals and refresh icon."""
        self._state = read_battery()
        self._peripherals = read_peripheral_batteries()
        self.present()
        return True

    def _power_tick(self) -> bool:
        """Cheap sysfs-only refresh of the live power reading."""
        if self._state is not None:
            self._state = self._state._replace(power_watts=read_power_watts())
            self.present()
        return True
