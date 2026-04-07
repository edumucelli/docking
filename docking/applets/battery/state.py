"""Pure state and parsing logic for Battery applet."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

from docking.i18n import _
from docking.log import get_logger

BAT_BASE = Path("/sys/class/power_supply")
log = get_logger("battery.state")


class BatteryState(NamedTuple):
    """Resolved battery info from sysfs."""

    icon_name: str  # FDO icon name (e.g. "battery-good-charging")
    capacity: int  # 0-100 percent
    status: str  # Kernel battery status (e.g. Charging / Discharging / Full)
    seconds_remaining: int | None  # Time estimate until empty/full when known


# Kernel capacity_level values -> FDO icon base names
_LEVEL_TO_ICON = {
    "full": "battery-full",
    "high": "battery-good",
    "normal": "battery-good",
    "low": "battery-low",
    "critical": "battery-caution",
    "unknown": "battery-empty",
}

_POWER_SETTINGS_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("gnome-control-center", "power"),
    ("mate-power-preferences",),
    ("xfce4-power-manager-settings",),
    ("kcmshell6", "powerdevilprofilesconfig"),
    ("kcmshell5", "powerdevilprofilesconfig"),
)


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
        seconds_remaining = _upower_seconds_remaining(bat_name=bat_name, status=status)
        if seconds_remaining is None:
            seconds_remaining = _estimate_seconds_remaining(
                bat_dir=bat_dir,
                status=status,
            )
    except (OSError, ValueError) as exc:
        log.debug("Failed to read battery state from %s: %s", bat_dir, exc)
        return None
    return BatteryState(
        icon_name=resolve_battery_icon(capacity_level=capacity_level, status=status),
        capacity=capacity,
        status=status,
        seconds_remaining=seconds_remaining,
    )


def power_settings_command() -> list[str] | None:
    """Return the first available desktop power-settings command."""
    for cmd in _POWER_SETTINGS_COMMANDS:
        if shutil.which(cmd[0]):
            return list(cmd)
    return None


def open_power_settings() -> bool:
    """Launch the desktop power-settings tool when one is available."""
    cmd = power_settings_command()
    if cmd is None:
        return False
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        log.bind(action="open_power_settings").warning("Failed to run %s: %s", cmd, exc)
        return False
    return True


def tooltip_text(state: BatteryState | None) -> str:
    """Build tooltip string from current state."""
    if state is None:
        return _("No battery")
    base = _("Battery: {pct}%").format(pct=state.capacity)
    estimate = _estimate_suffix(state=state)
    if estimate is None:
        return base
    return f"{base} • {estimate}"


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


def _upower_seconds_remaining(*, bat_name: str, status: str) -> int | None:
    if shutil.which("upower") is None:
        return None
    normalized = status.lower().strip()
    if normalized not in {"charging", "discharging"}:
        return None
    device = f"/org/freedesktop/UPower/devices/battery_{bat_name}"
    try:
        proc = subprocess.run(
            ["upower", "-i", device],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("Failed to query upower for %s: %s", bat_name, exc)
        return None
    if proc.returncode != 0:
        return None
    key = "time to full" if normalized == "charging" else "time to empty"
    return _parse_upower_duration_seconds(text=proc.stdout, key=key)


def _parse_upower_duration_seconds(*, text: str, key: str) -> int | None:
    pattern = rf"^\s*{re.escape(key)}:\s*(.+?)\s*$"
    for line in text.splitlines():
        match = re.match(pattern, line, re.IGNORECASE)
        if not match:
            continue
        return _parse_duration_seconds(raw=match.group(1))
    return None


def _parse_duration_seconds(*, raw: str) -> int | None:
    lowered = raw.strip().lower()
    if not lowered or lowered in {"n/a", "unknown", "0"}:
        return None

    total = 0.0
    matched = False
    pattern = r"([0-9]+(?:\.[0-9]+)?)\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)"
    for value, unit in re.findall(pattern, lowered):
        matched = True
        amount = float(value)
        if unit.startswith(("hour", "hr")):
            total += amount * 3600
        elif unit.startswith(("minute", "min")):
            total += amount * 60
        else:
            total += amount
    if not matched or total <= 0:
        return None
    return max(1, int(total))


def _estimate_seconds_remaining(*, bat_dir: Path, status: str) -> int | None:
    normalized = status.lower().strip()
    if normalized not in {"charging", "discharging"}:
        return None

    direct_name = (
        "time_to_full_now" if normalized == "charging" else "time_to_empty_now"
    )
    direct_value = _read_int(path=bat_dir / direct_name)
    if direct_value is not None and direct_value > 0:
        return direct_value

    energy_now = _read_int(path=bat_dir / "energy_now")
    energy_full = _read_int(path=bat_dir / "energy_full")
    power_now = _read_int(path=bat_dir / "power_now")
    if energy_now is not None and power_now is not None:
        remaining = (
            energy_now
            if normalized == "discharging"
            else _positive_delta(energy_full, energy_now)
        )
        return _seconds_from_rate(remaining=remaining, rate=power_now)

    charge_now = _read_int(path=bat_dir / "charge_now")
    charge_full = _read_int(path=bat_dir / "charge_full")
    current_now = _read_int(path=bat_dir / "current_now")
    if charge_now is not None and current_now is not None:
        remaining = (
            charge_now
            if normalized == "discharging"
            else _positive_delta(charge_full, charge_now)
        )
        return _seconds_from_rate(remaining=remaining, rate=current_now)

    return None


def _seconds_from_rate(*, remaining: int | None, rate: int | None) -> int | None:
    if remaining is None or rate is None or remaining <= 0 or rate <= 0:
        return None
    return max(1, int((remaining / rate) * 3600))


def _positive_delta(full: int | None, now: int | None) -> int | None:
    if full is None or now is None:
        return None
    delta = full - now
    if delta <= 0:
        return None
    return delta


def _read_int(*, path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None
