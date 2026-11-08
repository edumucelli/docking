"""Linux sensors and sysfs readers."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from docking.i18n import _

from ...backend import ThermalsBackend
from ...state import (
    HWMON_DIR,
    SENSORS_BIN,
    SENSORS_TIMEOUT_S,
    THERMAL_DIR,
    FanReading,
    ThermalReading,
    ThermalSnapshot,
    _clean_label,
    log,
    parse_sensors_output,
)


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
