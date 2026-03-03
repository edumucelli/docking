"""GTK lifecycle glue for Battery applet."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

from docking.applets.base import Applet
from docking.applets.battery.render import render_icon
from docking.applets.battery.state import BatteryState, read_battery, tooltip_text
from docking.applets.identity import AppletId

if TYPE_CHECKING:
    from docking.core.config import Config


class BatteryApplet(Applet):
    """Shows battery charge icon from sysfs, polled every 60 seconds."""

    id = AppletId.BATTERY
    name = "Battery"
    icon_name = "battery-good"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._state: BatteryState | None = read_battery()
        super().__init__(icon_size=icon_size, config=config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load battery theme icon matching current state."""
        return render_icon(size=size, state=self._state)

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

    def _tick(self) -> bool:
        """Re-read sysfs and refresh icon."""
        self._state = read_battery()
        self.refresh_presentation()
        return True
