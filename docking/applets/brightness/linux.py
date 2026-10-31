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

"""Linux brightness discovery and xrandr/backlight controls."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import NamedTuple

from docking.applets.brightness.backend import BrightnessBackend
from docking.applets.brightness.state import BrightnessState
from docking.log import get_logger
from docking.platform.environment import is_wayland_session

log = get_logger(name="brightness.linux")

_BACKLIGHT_DIR = Path("/sys/class/backlight")


class Display(NamedTuple):
    """A brightness backend with its xrandr output and optional sysfs path."""

    output: str  # xrandr output name (e.g. "eDP-1")
    sysfs: Path | None = None  # e.g. /sys/class/backlight/intel_backlight
    brightnessctl: str | None = None


def _run(cmd: list[str]) -> str | None:
    """Run command, return stdout or None on failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            return result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Failed to run %s: %s", cmd, exc)
    return None


def _find_sysfs_backlight() -> Path | None:
    """Find the first sysfs backlight directory, if any."""
    try:
        for entry in sorted(_BACKLIGHT_DIR.iterdir()):
            if (entry / "brightness").is_file() and (
                entry / "max_brightness"
            ).is_file():
                return entry
    except OSError as exc:
        log.debug("Failed to inspect %s: %s", _BACKLIGHT_DIR, exc)
    return None


def detect_output() -> Display | None:
    """Detect the primary connected xrandr output."""
    out = _run(cmd=["xrandr", "--listmonitors"])
    sysfs = _find_sysfs_backlight()
    brightnessctl = shutil.which("brightnessctl")
    if not out:
        if sysfs is not None and is_wayland_session():
            return Display(output="", sysfs=sysfs, brightnessctl=brightnessctl)
        return None
    # Format: " 0: +*HDMI-1 1920/480x1080/270+0+0  HDMI-1"
    monitors = [line.split() for line in out.strip().splitlines()[1:]]
    candidates = [parts for parts in monitors if len(parts) >= 4]
    if candidates:
        primary = next(
            (parts for parts in candidates if "*" in parts[1]), candidates[0]
        )
        return Display(output=primary[-1], sysfs=sysfs, brightnessctl=brightnessctl)
    if sysfs is not None and is_wayland_session():
        return Display(output="", sysfs=sysfs, brightnessctl=brightnessctl)
    return None


def get_brightness(backend: Display) -> float | None:
    """Read current brightness (0.0-1.0).

    Prefer X11 brightness on X11 sessions and backlight state on Wayland.
    """
    if is_wayland_session():
        if backend.sysfs:
            return _get_brightness_sysfs(path=backend.sysfs)
        return _get_brightness_xrandr(backend=backend)
    brightness = _get_brightness_xrandr(backend=backend)
    if brightness is not None:
        return brightness
    if backend.sysfs:
        return _get_brightness_sysfs(path=backend.sysfs)
    return None


def _get_brightness_sysfs(path: Path) -> float | None:
    """Read brightness from /sys/class/backlight as a 0.0-1.0 fraction."""
    try:
        current = int((path / "brightness").read_text().strip())
        maximum = int((path / "max_brightness").read_text().strip())
        if maximum > 0:
            return current / maximum
    except (OSError, ValueError) as exc:
        log.warning("Failed to read sysfs backlight: %s", exc)
    return None


def _get_brightness_xrandr(backend: Display) -> float | None:
    """Read brightness via xrandr --verbose (fallback, slow)."""
    out = _run(cmd=["xrandr", "--verbose"])
    if not out:
        return None
    in_output = False
    for line in out.splitlines():
        if line and not line[0].isspace() and line.split()[0] == backend.output:
            in_output = True
        elif line and not line[0].isspace():
            in_output = False
        if in_output:
            m = re.search(r"Brightness:\s+([\d.]+)", line)
            if m:
                return float(m.group(1))
    return None


def set_brightness(backend: Display, value: float) -> bool:
    """Set brightness (0.1-1.0), preferring X11 on X11 and backlight on Wayland."""
    clamped = max(0.1, min(1.0, value))
    xrandr_cmd = [
        "xrandr",
        "--output",
        backend.output,
        "--brightness",
        f"{clamped:.2f}",
    ]
    if is_wayland_session():
        if backend.brightnessctl:
            return (
                _run(cmd=[backend.brightnessctl, "set", f"{round(clamped * 100)}%"])
                is not None
            )
        if backend.sysfs:
            return _set_brightness_sysfs(path=backend.sysfs, value=clamped)
        return _run(cmd=xrandr_cmd) is not None
    if _run(cmd=xrandr_cmd) is not None:
        return True
    if backend.brightnessctl:
        return (
            _run(cmd=[backend.brightnessctl, "set", f"{round(clamped * 100)}%"])
            is not None
        )
    if backend.sysfs:
        return _set_brightness_sysfs(path=backend.sysfs, value=clamped)
    return False


def _set_brightness_sysfs(path: Path, value: float) -> bool:
    """Write brightness to /sys/class/backlight as an integer level."""
    try:
        maximum = int((path / "max_brightness").read_text().strip())
        if maximum <= 0:
            return False
        current = max(1, round(maximum * value))
        (path / "brightness").write_text(f"{current}\n")
        return True
    except (OSError, ValueError) as exc:
        log.warning("Failed to write sysfs backlight: %s", exc)
    return False


class LinuxBrightnessBackend(BrightnessBackend):
    def __init__(self) -> None:
        self._stopped = threading.Event()
        self._lock = threading.Lock()

    def _read(self) -> tuple[Display | None, BrightnessState]:
        if self._stopped.is_set():
            return None, BrightnessState()
        display = detect_output()
        if display is None:
            return None, BrightnessState()
        level = get_brightness(display)
        target = f"{display.output}|{display.sysfs or ''}"
        return display, BrightnessState(level=level, target=target)

    def get_state(self) -> BrightnessState:
        with self._lock:
            return self._read()[1]

    def set_brightness(self, *, value: float, target: str) -> BrightnessState:
        with self._lock:
            display, state = self._read()
            if (
                display is None
                or not state.available
                or state.target != target
                or not math.isfinite(value)
            ):
                return state
            if not self._stopped.is_set():
                set_brightness(display, value)
            return self._read()[1]

    def stop(self) -> None:
        self._stopped.set()
