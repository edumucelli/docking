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

"""Pure state and parsing logic for Battery applet."""

from __future__ import annotations

from typing import NamedTuple

from docking.i18n import _

# Bottom-center overlay modes (mutually exclusive, mirroring the network applet).
OVERLAY_NONE = "none"
OVERLAY_PERCENT = "percent"
OVERLAY_POWER = "power"
OVERLAY_MODES = (OVERLAY_NONE, OVERLAY_PERCENT, OVERLAY_POWER)


class BatteryState(NamedTuple):
    """Resolved battery status; an unknown percentage is distinct from zero."""

    icon_name: str  # FDO icon name (e.g. "battery-good-charging")
    capacity: int | None  # 0-100 percent, or unknown
    status: str  # Kernel battery status (e.g. Charging / Discharging / Full)
    seconds_remaining: int | None  # Time estimate until empty/full when known
    power_watts: float | None = None  # Instantaneous battery power draw/input


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


def overlay_from_prefs(prefs: dict[str, object]) -> str:
    """Resolve the overlay mode, migrating the legacy ``show_percent`` flag."""
    mode = prefs.get("overlay")
    if mode in OVERLAY_MODES:
        return str(mode)
    return OVERLAY_PERCENT if prefs.get("show_percent") else OVERLAY_NONE


def format_power(watts: float, *, compact: bool = False) -> str:
    """Format watts as e.g. ``7.6 W`` (or ``7.6W`` for the compact overlay)."""
    digits = f"{watts:.1f}" if watts < 10 else f"{watts:.0f}"
    return f"{digits}W" if compact else f"{digits} W"


def tooltip_text(state: BatteryState | None) -> str:
    """Build tooltip string from current state."""
    if state is None:
        return _("No battery")
    parts = [
        _("Battery: {pct}%").format(pct=state.capacity)
        if state.capacity is not None
        else f"{_('Battery')}: {_('Unknown')}"
    ]
    estimate = _estimate_suffix(state=state)
    if estimate is not None:
        parts.append(estimate)
    power = _power_suffix(state=state)
    if power is not None:
        parts.append(power)
    return " • ".join(parts)


def _power_suffix(state: BatteryState) -> str | None:
    if state.power_watts is None or state.power_watts <= 0:
        return None
    return format_power(state.power_watts)


def _estimate_suffix(state: BatteryState) -> str | None:
    time_text = _format_duration(seconds=state.seconds_remaining)
    if time_text is None:
        return None
    status = state.status.lower().strip()
    if status == "discharging":
        return _("{time} left").format(time=time_text)
    if status == "charging":
        return _("{time} until full").format(time=time_text)
    return None


def _format_duration(seconds: int | None) -> str | None:
    if seconds is None or seconds <= 0:
        return None
    total_minutes = max(1, int((seconds + 30) // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours > 0:
        return _("{hours}h {minutes:02d}m").format(hours=hours, minutes=minutes)
    return _("{minutes}m").format(minutes=minutes)
