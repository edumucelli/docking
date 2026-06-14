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

"""CPU temperature detection helpers for System Monitor applet."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from docking.log import get_logger

CPU_TEMP_COMMAND_TIMEOUT_S = 0.25
_THERMAL_ROOT = Path("/sys/class/thermal")
_HWMON_ROOT = Path("/sys/class/hwmon")
log = get_logger("systemmonitor.temperature")


@dataclass(frozen=True)
class CommandBackend:
    """One optional external command source for CPU temperature."""

    command: str
    argv: tuple[str, ...]


_COMMAND_BACKENDS = (
    CommandBackend(command="sensors", argv=("sensors",)),
    CommandBackend(command="vcgencmd", argv=("vcgencmd", "measure_temp")),
    CommandBackend(command="acpi", argv=("acpi", "-t")),
)

_CHIP_HINT_SCORES = (
    ("coretemp", 90),
    ("k10temp", 90),
    ("zenpower", 90),
    ("cpu_thermal", 85),
    ("x86_pkg_temp", 85),
    ("fam15h_power", 80),
    ("soc_thermal", 40),
    ("acpitz", 20),
)
_LABEL_HINT_SCORES = (
    ("package id", 100),
    ("package", 95),
    ("tctl", 95),
    ("tdie", 90),
    ("cpu", 80),
    ("core", 60),
    ("temp1", 20),
)
_THERMAL_TYPE_SCORES = (
    ("x86_pkg_temp", 100),
    ("package", 95),
    ("cpu", 90),
    ("cpu_thermal", 90),
    ("tctl", 90),
    ("tdie", 90),
    ("soc", 40),
    ("acpitz", 15),
)
_SENSOR_LINE_RE = re.compile(
    r"^\s*([^:]+):\s*\+?(-?\d+(?:\.\d+)?)\s*(?:°C|degrees C)",
    re.IGNORECASE,
)
_ACPI_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*degrees?\s*C", re.IGNORECASE)
_VCGENCMD_RE = re.compile(r"temp=\s*(-?\d+(?:\.\d+)?)'")


def parse_sysfs_temperature(text: str) -> float | None:
    """Parse a sysfs temperature file into celsius if it is valid."""
    raw = text.strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        log.debug("Invalid sysfs temperature value %r: %s", raw, exc)
        return None
    if value >= 1000:
        value /= 1000.0
    if value <= 0:
        return None
    return value


class TemperatureReader:
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


def parse_sensors_output(text: str) -> float | None:
    """Parse lm-sensors output into the best CPU-like temperature."""
    best: tuple[int, float] | None = None
    current_chip = ""

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
        match = _SENSOR_LINE_RE.match(stripped)
        if not match:
            continue
        label = match.group(1).strip()
        temperature = parse_sysfs_temperature(match.group(2))
        if temperature is None:
            continue
        score = max(
            _score_text(current_chip, _CHIP_HINT_SCORES),
            _score_text(label, _LABEL_HINT_SCORES),
        )
        if (
            best is None
            or score > best[0]
            or (score == best[0] and temperature > best[1])
        ):
            best = (score, temperature)

    if best is None:
        return None
    return best[1]


def parse_vcgencmd_output(text: str) -> float | None:
    """Parse Raspberry Pi vcgencmd output."""
    match = _VCGENCMD_RE.search(text)
    if not match:
        return None
    return parse_sysfs_temperature(match.group(1))


def parse_acpi_output(text: str) -> float | None:
    """Parse acpi thermal-zone output."""
    temperatures = [
        temperature
        for temperature in (
            parse_sysfs_temperature(match) for match in _ACPI_RE.findall(text)
        )
        if temperature is not None
    ]
    if not temperatures:
        return None
    return max(temperatures)


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


def _score_text(text: str, hints: tuple[tuple[str, int], ...]) -> int:
    lowered = text.lower()
    best = 0
    for hint, score in hints:
        if hint in lowered:
            best = max(best, score)
    return best


def _pick_best(
    current: tuple[int, float, Path] | None,
    *,
    score: int,
    temperature: float,
    path: Path,
) -> tuple[int, float, Path]:
    if current is None:
        return (score, temperature, path)
    if score > current[0]:
        return (score, temperature, path)
    if score == current[0] and temperature > current[1]:
        return (score, temperature, path)
    return current
