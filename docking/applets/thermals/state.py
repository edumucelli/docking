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

"""Thermal backend, parsing, and formatting helpers for the Thermals applet."""

from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docking.applets import temperature as temperature_display
from docking.applets.live_state import (
    live_freshness_lines,
    live_state_error,
    live_state_label,
    refresh_recovery_label,
    resolve_live_status,
)
from docking.applets.tooltip import structured_tooltip
from docking.core.math import clamp
from docking.i18n import _
from docking.log import get_logger

SENSORS_BIN = "sensors"
SENSORS_TIMEOUT_S = 0.6
REFRESH_INTERVAL_S = 5
STARTUP_FETCH_DELAY_S = 1
HWMON_DIR = Path("/sys/class/hwmon")
THERMAL_DIR = Path("/sys/class/thermal")
TemperatureUnit = temperature_display.TemperatureUnit
format_temperature_compact = temperature_display.format_temperature_compact
normalize_temperature_unit = temperature_display.normalize_temperature_unit
temperature_unit_label = temperature_display.temperature_unit_label

log = get_logger(name="thermals.state")

_TEMP_RE = re.compile(
    r"^\s*([^:]+):\s*\+?(-?\d+(?:\.\d+)?)\s*(?:C|\N{DEGREE SIGN}C)\b",
    re.IGNORECASE,
)
_FAN_RE = re.compile(
    r"^\s*([^:]+):\s*(\d+(?:\.\d+)?)\s*RPM\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ThermalReading:
    """One temperature reading from lm-sensors."""

    chip: str
    label: str
    celsius: float


@dataclass(frozen=True, slots=True)
class FanReading:
    """One fan speed reading from lm-sensors."""

    chip: str
    label: str
    rpm: int


@dataclass(frozen=True, slots=True)
class ThermalSnapshot:
    """Current hottest temperature plus fastest fan."""

    available: bool
    hottest: ThermalReading | None = None
    fan: FanReading | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class ThermalsPrefs:
    """Persisted Thermals applet preferences."""

    temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS


class ThermalsBackend:
    """Backend interface for reading current thermal state."""

    def get_snapshot(self) -> ThermalSnapshot:
        """Read current thermal state."""
        raise NotImplementedError

    @staticmethod
    def has_readings(snapshot: ThermalSnapshot) -> bool:
        """Return whether a snapshot contains live thermal data."""
        return snapshot.hottest is not None or snapshot.fan is not None


class SensorsThermalsBackend(ThermalsBackend):
    """Thermals backend backed by the ``sensors`` command."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._which = which
        self._run = run

    def get_snapshot(self) -> ThermalSnapshot:
        """Read current thermal state from lm-sensors."""
        if self._which(SENSORS_BIN) is None:
            return ThermalSnapshot(
                available=False,
                error=_("lm-sensors not installed"),
            )
        try:
            result = self._run(
                [SENSORS_BIN],
                capture_output=True,
                text=True,
                timeout=SENSORS_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("Failed to run sensors: %s", exc)
            return ThermalSnapshot(
                available=True,
                error=str(exc) or exc.__class__.__name__,
            )

        if result.returncode != 0:
            error = (result.stderr or result.stdout).strip()
            return ThermalSnapshot(
                available=True,
                error=error or _("sensors failed"),
            )

        snapshot = parse_sensors_output(result.stdout)
        if not self.has_readings(snapshot):
            return ThermalSnapshot(
                available=True,
                error=_("No thermal readings"),
            )
        return snapshot


class SysfsThermalsBackend(ThermalsBackend):
    """Thermals backend backed by kernel hwmon and thermal-zone sysfs."""

    def __init__(
        self,
        *,
        hwmon_dir: Path = HWMON_DIR,
        thermal_dir: Path = THERMAL_DIR,
    ) -> None:
        self._hwmon_dir = hwmon_dir
        self._thermal_dir = thermal_dir

    def get_snapshot(self) -> ThermalSnapshot:
        """Read thermal state directly from kernel sysfs."""
        hottest: ThermalReading | None = None
        fastest_fan: FanReading | None = None

        for reading in self._read_hwmon_temperatures():
            if hottest is None or reading.celsius > hottest.celsius:
                hottest = reading
        for reading in self._read_thermal_zone_temperatures():
            if hottest is None or reading.celsius > hottest.celsius:
                hottest = reading
        for reading in self._read_hwmon_fans():
            if fastest_fan is None or reading.rpm > fastest_fan.rpm:
                fastest_fan = reading

        return ThermalSnapshot(
            available=True,
            hottest=hottest,
            fan=fastest_fan,
        )

    def _read_hwmon_temperatures(self) -> list[ThermalReading]:
        readings: list[ThermalReading] = []
        for chip_dir in self._iter_dirs(self._hwmon_dir, prefix="hwmon"):
            chip = self._read_text(chip_dir / "name") or chip_dir.name
            for input_path in sorted(chip_dir.glob("temp*_input")):
                celsius = self._read_millivalue(input_path)
                if celsius is None:
                    continue
                sensor_id = input_path.name.removeprefix("temp").removesuffix("_input")
                label = (
                    self._read_text(chip_dir / f"temp{sensor_id}_label")
                    or input_path.name
                )
                readings.append(
                    ThermalReading(
                        chip=chip,
                        label=_clean_label(label),
                        celsius=celsius,
                    )
                )
        return readings

    def _read_thermal_zone_temperatures(self) -> list[ThermalReading]:
        readings: list[ThermalReading] = []
        for zone_dir in self._iter_dirs(self._thermal_dir, prefix="thermal_zone"):
            celsius = self._read_millivalue(zone_dir / "temp")
            if celsius is None:
                continue
            label = self._read_text(zone_dir / "type") or zone_dir.name
            readings.append(
                ThermalReading(
                    chip=zone_dir.name,
                    label=_clean_label(label),
                    celsius=celsius,
                )
            )
        return readings

    def _read_hwmon_fans(self) -> list[FanReading]:
        readings: list[FanReading] = []
        for chip_dir in self._iter_dirs(self._hwmon_dir, prefix="hwmon"):
            chip = self._read_text(chip_dir / "name") or chip_dir.name
            for input_path in sorted(chip_dir.glob("fan*_input")):
                rpm = self._read_int(input_path)
                if rpm is None:
                    continue
                sensor_id = input_path.name.removeprefix("fan").removesuffix("_input")
                label = (
                    self._read_text(chip_dir / f"fan{sensor_id}_label")
                    or input_path.name
                )
                readings.append(
                    FanReading(
                        chip=chip,
                        label=_clean_label(label),
                        rpm=rpm,
                    )
                )
        return readings

    @staticmethod
    def _iter_dirs(path: Path, *, prefix: str) -> list[Path]:
        try:
            return sorted(
                entry
                for entry in path.iterdir()
                if entry.name.startswith(prefix) and entry.is_dir()
            )
        except OSError as exc:
            log.debug("Failed to inspect %s: %s", path, exc)
        return []

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text().strip()
        except OSError:
            return ""

    @classmethod
    def _read_int(cls, path: Path) -> int | None:
        try:
            return int(cls._read_text(path))
        except ValueError:
            return None

    @classmethod
    def _read_millivalue(cls, path: Path) -> float | None:
        value = cls._read_int(path)
        if value is None:
            return None
        if abs(value) >= 1000:
            return value / 1000.0
        return float(value)


class FallbackThermalsBackend(ThermalsBackend):
    """Thermals backend that falls back when the primary has no readings."""

    def __init__(
        self,
        *,
        primary: ThermalsBackend,
        fallback: ThermalsBackend,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def get_snapshot(self) -> ThermalSnapshot:
        """Return primary readings, or fallback readings when primary is empty."""
        primary_snapshot = self._primary.get_snapshot()
        if self.has_readings(primary_snapshot):
            return primary_snapshot

        fallback_snapshot = self._fallback.get_snapshot()
        if self.has_readings(fallback_snapshot):
            return fallback_snapshot
        return primary_snapshot


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> ThermalsPrefs:
    """Build preferences from persisted values."""
    if not prefs:
        return ThermalsPrefs()
    return ThermalsPrefs(
        temperature_unit=normalize_temperature_unit(prefs.get("temperature_unit")),
    )


def prefs_payload(*, temperature_unit: TemperatureUnit) -> dict[str, object]:
    """Build payload used by save_prefs()."""
    return {"temperature_unit": normalize_temperature_unit(temperature_unit).value}


def parse_sensors_output(text: str) -> ThermalSnapshot:
    """Parse human-readable ``sensors`` output.

    The applet cares about the hottest displayed temperature and the highest
    reported fan RPM. It does not try to infer CPU-only labels; the goal is to
    mirror lm-sensors' current view of the machine.
    """
    current_chip = ""
    hottest: ThermalReading | None = None
    fastest_fan: FanReading | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            current_chip = ""
            continue
        if ":" not in stripped and not line[:1].isspace():
            current_chip = stripped
            continue
        if stripped.lower().startswith("adapter:"):
            continue

        temp_match = _TEMP_RE.match(stripped)
        if temp_match:
            reading = ThermalReading(
                chip=current_chip,
                label=_clean_label(temp_match.group(1)),
                celsius=float(temp_match.group(2)),
            )
            if hottest is None or reading.celsius > hottest.celsius:
                hottest = reading
            continue

        fan_match = _FAN_RE.match(stripped)
        if fan_match:
            reading = FanReading(
                chip=current_chip,
                label=_clean_label(fan_match.group(1)),
                rpm=round(float(fan_match.group(2))),
            )
            if fastest_fan is None or reading.rpm > fastest_fan.rpm:
                fastest_fan = reading

    return ThermalSnapshot(available=True, hottest=hottest, fan=fastest_fan)


def read_thermal_snapshot(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    hwmon_dir: Path = HWMON_DIR,
    thermal_dir: Path = THERMAL_DIR,
) -> ThermalSnapshot:
    """Read current thermal state from lm-sensors or kernel sysfs."""
    backend = FallbackThermalsBackend(
        primary=SensorsThermalsBackend(which=which, run=run),
        fallback=SysfsThermalsBackend(hwmon_dir=hwmon_dir, thermal_dir=thermal_dir),
    )
    return backend.get_snapshot()


def read_sysfs_thermal_snapshot(
    *,
    hwmon_dir: Path = HWMON_DIR,
    thermal_dir: Path = THERMAL_DIR,
) -> ThermalSnapshot:
    """Read thermal state directly from kernel hwmon and thermal-zone sysfs."""
    return SysfsThermalsBackend(
        hwmon_dir=hwmon_dir,
        thermal_dir=thermal_dir,
    ).get_snapshot()


def build_tooltip(
    *,
    snapshot: ThermalSnapshot | None,
    loading: bool = False,
    error: str | None = None,
    temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
    updated_at: dt.datetime | str | None = None,
    cadence_seconds: int | None = None,
) -> str:
    """Build tooltip text for Thermals."""
    snapshot_error = snapshot.error if snapshot is not None else ""
    state_error = error or snapshot_error
    status = resolve_live_status(
        has_data=snapshot is not None and snapshot.available and not snapshot.error,
        loading=loading,
        error=state_error,
        updated_at=updated_at,
    )
    if snapshot is None:
        return structured_tooltip(
            title=_("Thermals"),
            primary=live_state_label(status),
            freshness=live_freshness_lines(
                status=status,
                updated_at=updated_at,
                cadence_seconds=cadence_seconds,
                cadence_verb=_("Samples"),
            ),
            error=live_state_error(status=status, error=state_error),
            recovery=refresh_recovery_label(status),
        )
    if not snapshot.available:
        return structured_tooltip(
            title=_("Thermals"),
            freshness=live_freshness_lines(
                status=status,
                updated_at=updated_at,
                cadence_seconds=cadence_seconds,
                cadence_verb=_("Samples"),
            ),
            error=snapshot.error or _("lm-sensors not installed"),
            recovery=refresh_recovery_label(status),
        )
    if snapshot.error:
        return structured_tooltip(
            title=_("Thermals"),
            freshness=live_freshness_lines(
                status=status,
                updated_at=updated_at,
                cadence_seconds=cadence_seconds,
                cadence_verb=_("Samples"),
            ),
            error=snapshot.error,
            recovery=refresh_recovery_label(status),
        )

    primary = None
    details = []
    if snapshot.hottest is not None:
        primary = _("Hot: {label} {temp}").format(
            label=reading_label(snapshot.hottest),
            temp=format_temperature(
                snapshot.hottest.celsius,
                temperature_unit=temperature_unit,
            ),
        )
    else:
        primary = _("Hot: unavailable")

    if snapshot.fan is not None:
        details.append(
            _("Fan: {label} {rpm}").format(
                label=reading_label(snapshot.fan),
                rpm=format_rpm(snapshot.fan.rpm),
            )
        )
    else:
        details.append(_("Fan: unavailable"))
    return structured_tooltip(
        title=_("Thermals"),
        primary=primary,
        details=details,
        freshness=live_freshness_lines(
            status=status,
            updated_at=updated_at,
            cadence_seconds=cadence_seconds,
            cadence_verb=_("Samples"),
        ),
        error=live_state_error(status=status, error=state_error),
        recovery=refresh_recovery_label(status),
    )


def reading_label(reading: ThermalReading | FanReading) -> str:
    """Return a compact label that keeps chip context when useful."""
    if reading.chip:
        return f"{reading.chip} {reading.label}"
    return reading.label


def format_temperature(
    celsius: float | None,
    *,
    temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
) -> str:
    """Format full thermal readings with one decimal and a unit suffix."""
    return temperature_display.format_temperature(
        celsius,
        temperature_unit=temperature_unit,
        precision=1,
    )


def format_rpm(rpm: int | None) -> str:
    if rpm is None:
        return "--"
    return f"{rpm} RPM"


def format_rpm_compact(rpm: int | None) -> str:
    if rpm is None:
        return "--"
    if rpm >= 10_000:
        return f"{rpm // 1000}k"
    if rpm >= 1000:
        return f"{rpm / 1000:.1f}k"
    return str(rpm)


def thermal_level(celsius: float | None) -> float:
    """Map temperature to a 0..1 visual severity value."""
    if celsius is None:
        return 0.0
    return clamp((celsius - 35.0) / 55.0, 0.0, 1.0)


def thermal_color(celsius: float | None) -> tuple[float, float, float]:
    """Green -> amber -> red color ramp for temperature."""
    level = thermal_level(celsius)
    if level < 0.5:
        ratio = level / 0.5
        return (0.18 + 0.72 * ratio, 0.72 - 0.17 * ratio, 0.38 - 0.22 * ratio)
    ratio = (level - 0.5) / 0.5
    return (0.90 + 0.04 * ratio, 0.55 - 0.34 * ratio, 0.16 - 0.06 * ratio)


def _clean_label(value: str) -> str:
    return " ".join(value.strip().split())
