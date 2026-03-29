"""Pure state and parsing logic for Battery applet."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from docking.i18n import _

BAT_BASE = Path("/sys/class/power_supply")


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
    """Map sysfs capacity_level + status to FDO icon name."""
    base = _LEVEL_TO_ICON.get(capacity_level.lower().strip(), "battery-missing")
    if status.lower().strip() in ("charging", "full"):
        base += "-charging"
    return base


def read_battery(bat_name: str = "BAT0", base: Path = BAT_BASE) -> BatteryState | None:
    """Read battery state from sysfs. Returns None if battery not found."""
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
        icon_name=resolve_battery_icon(capacity_level=capacity_level, status=status),
        capacity=capacity,
    )


def tooltip_text(state: BatteryState | None) -> str:
    """Build tooltip string from current state."""
    if state is None:
        return _("No battery")
    return _("Battery: {pct}%").format(pct=state.capacity)
