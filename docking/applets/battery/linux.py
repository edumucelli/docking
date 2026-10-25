"""Linux battery readings from sysfs and UPower."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from docking.applets.battery.backend import BatteryBackend
from docking.applets.battery.desktop_settings import (
    open_power_settings,
    power_settings_command,
)
from docking.applets.battery.peripherals import PeripheralBattery
from docking.applets.battery.state import (
    BatteryState,
    resolve_battery_icon,
)
from docking.applets.battery.upower import (
    _parse_upower_duration_seconds,
    read_peripheral_batteries,
)
from docking.log import get_logger

log = get_logger("battery.linux")
BAT_BASE = Path("/sys/class/power_supply")


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
        power_watts = _read_power_watts(bat_dir=bat_dir)
    except (OSError, ValueError) as exc:
        log.debug("Failed to read battery state from %s: %s", bat_dir, exc)
        return None
    return BatteryState(
        icon_name=resolve_battery_icon(capacity_level=capacity_level, status=status),
        capacity=capacity,
        status=status,
        seconds_remaining=seconds_remaining,
        power_watts=power_watts,
    )


def read_power_watts(bat_name: str = "BAT0", base: Path = BAT_BASE) -> float | None:
    """Read just the instantaneous battery power in watts (no subprocess).

    Cheap sysfs-only read used for the live power overlay's fast refresh, kept
    separate from ``read_battery`` so the frequent poll never spawns ``upower``.
    """
    bat_dir = base / bat_name
    if not bat_dir.exists():
        return None
    return _read_power_watts(bat_dir=bat_dir)


def _read_power_watts(*, bat_dir: Path) -> float | None:
    """Resolve battery power in watts from sysfs.

    Prefers ``power_now`` (microwatts); falls back to ``current_now`` (microamps)
    times ``voltage_now`` (microvolts) for charge-based batteries. The value is a
    magnitude; direction (draw vs input) comes from ``status``.
    """
    power_now = _read_int(path=bat_dir / "power_now")
    if power_now is not None:
        return power_now / 1_000_000 if power_now >= 0 else None

    current_now = _read_int(path=bat_dir / "current_now")
    voltage_now = _read_int(path=bat_dir / "voltage_now")
    if current_now is not None and voltage_now is not None:
        return (current_now * voltage_now) / 1e12
    return None


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


def _read_int(*, path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


class LinuxBatteryBackend(BatteryBackend):
    def __init__(self, bat_name: str = "BAT0", base: Path = BAT_BASE) -> None:
        self._bat_name = bat_name
        self._base = base

    def read_battery(self) -> BatteryState | None:
        return read_battery(self._bat_name, self._base)

    def read_power_watts(self) -> float | None:
        return read_power_watts(self._bat_name, self._base)

    def read_peripherals(self) -> list[PeripheralBattery]:
        return read_peripheral_batteries()

    def settings_available(self) -> bool:
        return power_settings_command() is not None

    def open_settings(self) -> bool:
        return open_power_settings()


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
