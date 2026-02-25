"""Battery docklet -- shows charge level and charging state from sysfs.

Reads /sys/class/power_supply/BAT0/ every 60 seconds. Maps capacity_level
to standard FDO battery icon names (battery-full, battery-good, battery-low,
battery-caution, battery-empty) with -charging suffix when on AC.
Tooltip shows percentage (e.g. "85%").
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, NamedTuple

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

from docking.docklets.base import Docklet

if TYPE_CHECKING:
    from docking.core.config import Config

BAT_BASE = Path("/sys/class/power_supply")


# -- Pure data functions (testable without GTK) ------------------------------


class BatteryState(NamedTuple):
    """Resolved battery info from sysfs."""

    icon_name: str  # FDO icon name (e.g. "battery-good-charging")
    capacity: int  # 0-100 percent


# Kernel capacity_level values -> FDO icon base names
_LEVEL_TO_ICON = {
    "full": "battery-full",
    "high": "battery-good",
    "normal": "battery-good",
    "low": "battery-low",
    "critical": "battery-caution",
    "unknown": "battery-empty",
}


def resolve_battery_icon(capacity_level: str, status: str) -> str:
    """Map sysfs capacity_level + status to FDO icon name.

    Appends '-charging' suffix when status is Charging or Full (AC connected).
    Returns 'battery-missing' for unrecognized capacity levels.
    """
    base = _LEVEL_TO_ICON.get(capacity_level.lower().strip(), "battery-missing")
    if status.lower().strip() in ("charging", "full"):
        base += "-charging"
    return base


def read_battery(bat_name: str = "BAT0", base: Path = BAT_BASE) -> BatteryState | None:
    """Read battery state from sysfs. Returns None if battery not found.

    Reads three files from /sys/class/power_supply/{bat_name}/:
      capacity       -- integer 0-100
      capacity_level -- full/high/normal/low/critical/unknown
      status         -- Charging/Discharging/Full/Not charging/Unknown
    """
    bat_dir = base / bat_name
    if not bat_dir.exists():
        return None
    try:
        capacity = int((bat_dir / "capacity").read_text().strip())
        capacity_level = (bat_dir / "capacity_level").read_text().strip()
        status = (bat_dir / "status").read_text().strip()
    except (OSError, ValueError):
        return None
    return BatteryState(
        icon_name=resolve_battery_icon(capacity_level, status),
        capacity=capacity,
    )


def _load_theme_icon(name: str, size: int) -> GdkPixbuf.Pixbuf | None:
    """Load icon from theme, centered on a square canvas.

    Battery icons are often non-square (taller than wide). This loads
    the icon and composites it centered on a transparent square pixbuf
    so the dock renderer gets a uniform size.
    """
    theme = Gtk.IconTheme.get_default()
    try:
        raw = theme.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
    except GLib.Error:
        return None
    if raw is None:
        return None
    w, h = raw.get_width(), raw.get_height()
    if w == h:
        return raw
    # Center on transparent square canvas to preserve aspect ratio
    canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    canvas.fill(0x00000000)
    x = (size - w) // 2
    y = (size - h) // 2
    raw.composite(
        canvas, x, y, w, h, x, y, 1.0, 1.0, GdkPixbuf.InterpType.BILINEAR, 255
    )
    return canvas


# -- Docklet -----------------------------------------------------------------


class BatteryDocklet(Docklet):
    """Shows battery charge icon from sysfs, polled every 60 seconds.

    No preferences, no menu items. Tooltip shows percentage.
    """

    id = "battery"
    name = "Battery"
    icon_name = "battery-good"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._state: BatteryState | None = read_battery()
        super().__init__(icon_size, config)
        # Set tooltip immediately (create_icon can't on first call
        # because item doesn't exist yet during super().__init__)
        if self._state:
            self.item.name = f"{self._state.capacity}%"
        else:
            self.item.name = "No battery"

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load battery theme icon matching current state; update tooltip."""
        if self._state:
            icon_name = self._state.icon_name
            if hasattr(self, "item"):
                self.item.name = f"{self._state.capacity}%"
        else:
            icon_name = "battery-missing"
            if hasattr(self, "item"):
                self.item.name = "No battery"
        return _load_theme_icon(icon_name, size)

    def start(self, notify: Callable[[], None]) -> None:
        """Start 60-second polling timer (battery changes slowly)."""
        super().start(notify)
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
        self.refresh_icon()
        return True
