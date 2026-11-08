from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from ...backend import TemperatureSource
from ...temperature import (
    _CHIP_HINT_SCORES,
    _COMMAND_BACKENDS,
    _HWMON_ROOT,
    _LABEL_HINT_SCORES,
    _THERMAL_ROOT,
    _THERMAL_TYPE_SCORES,
    CPU_TEMP_COMMAND_TIMEOUT_S,
    CommandBackend,
    _pick_best,
    _score_text,
    log,
    parse_acpi_output,
    parse_sensors_output,
    parse_sysfs_temperature,
    parse_vcgencmd_output,
)


def discover_sysfs_temperature_path(
    *,
    thermal_root: Path,
    hwmon_root: Path,
) -> Path | None:
    """Find the most CPU-like temperature file in Linux sysfs."""
    best: tuple[int, float, Path] | None = None

    for zone in sorted(thermal_root.glob("thermal_zone*")):
        temp_path = zone / "temp"
        zone_type = _safe_read_text(zone / "type") or ""
        score = _score_text(zone_type, _THERMAL_TYPE_SCORES)
        if score <= 0:
            continue
        temperature = read_temperature_file(temp_path)
        if temperature is None:
            continue
        best = _pick_best(best, score=score, temperature=temperature, path=temp_path)

    for hwmon in sorted(hwmon_root.glob("hwmon*")):
        chip_name = (_safe_read_text(hwmon / "name") or "").strip()
        chip_score = _score_text(chip_name, _CHIP_HINT_SCORES)
        for temp_path in sorted(hwmon.glob("temp*_input")):
            stem = temp_path.name.removesuffix("_input")
            label = (_safe_read_text(hwmon / f"{stem}_label") or "").strip()
            score = max(chip_score, _score_text(label, _LABEL_HINT_SCORES))
            if chip_score > 0 and temp_path.name == "temp1_input":
                score = max(score, chip_score)
            if score <= 0:
                continue
            temperature = read_temperature_file(temp_path)
            if temperature is None:
                continue
            best = _pick_best(
                best,
                score=score,
                temperature=temperature,
                path=temp_path,
            )

    if best is None:
        return None
    return best[2]


def discover_available_commands(
    *, which: Callable[[str], str | None]
) -> tuple[CommandBackend, ...]:
    """Return installed command backends in preference order."""
    available: list[CommandBackend] = []
    for backend in _COMMAND_BACKENDS:
        if which(backend.command):
            available.append(backend)
    return tuple(available)


def read_temperature_file(path: Path) -> float | None:
    """Read one sysfs temperature file."""
    text = _safe_read_text(path)
    if text is None:
        return None
    return parse_sysfs_temperature(text)


def read_command_temperature(
    *,
    backend: CommandBackend,
    run_command: Callable[[list[str], float], str | None],
) -> float | None:
    """Run one command backend and parse its temperature output."""
    text = run_command(list(backend.argv), CPU_TEMP_COMMAND_TIMEOUT_S)
    if text is None:
        return None
    if backend.command == "sensors":
        return parse_sensors_output(text)
    if backend.command == "vcgencmd":
        return parse_vcgencmd_output(text)
    if backend.command == "acpi":
        return parse_acpi_output(text)
    return None


def _run_command(cmd: list[str], timeout_s: float) -> str | None:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("Failed to run temperature command %s: %s", cmd, exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _safe_read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.debug("Failed to read temperature file %s: %s", path, exc)
        return None


class LinuxTemperatureReader(TemperatureSource):
    """Discover and read CPU temperature from sysfs and common CLI tools."""

    def __init__(
        self,
        *,
        thermal_root: Path = _THERMAL_ROOT,
        hwmon_root: Path = _HWMON_ROOT,
        which: Callable[[str], str | None] = shutil.which,
        run_command: Callable[[list[str], float], str | None] | None = None,
    ) -> None:
        self._thermal_root = thermal_root
        self._hwmon_root = hwmon_root
        self._which = which
        self._run_command = run_command or _run_command
        self._sysfs_path: Path | None = None
        self._sysfs_probed = False
        self._command_backends: tuple[CommandBackend, ...] = ()
        self._commands_probed = False

    def read(self) -> float | None:
        """Return current CPU temperature in celsius if one can be found."""
        temperature = self._read_from_sysfs()
        if temperature is not None:
            return temperature
        return self._read_from_commands()

    def _read_from_sysfs(self) -> float | None:
        if self._sysfs_path is None and not self._sysfs_probed:
            self._sysfs_path = discover_sysfs_temperature_path(
                thermal_root=self._thermal_root,
                hwmon_root=self._hwmon_root,
            )
            self._sysfs_probed = True
        if self._sysfs_path is None:
            return None
        temperature = read_temperature_file(self._sysfs_path)
        if temperature is None:
            self._sysfs_path = None
            self._sysfs_probed = False
        return temperature

    def _read_from_commands(self) -> float | None:
        if not self._commands_probed:
            self._command_backends = discover_available_commands(which=self._which)
            self._commands_probed = True
        for backend in self._command_backends:
            temperature = read_command_temperature(
                backend=backend,
                run_command=self._run_command,
            )
            if temperature is not None:
                return temperature
        return None
