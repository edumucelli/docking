"""Unix native operations extracted without changing fallback policy."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

from docking.applets.brightness.backend import (
    BrightnessOperations as BrightnessOperationsBase,
)
from docking.platform.environment import is_wayland_session

from ...backend import BacklightSource
from ...state import log


class Backend(NamedTuple):
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


def detect_output(backlight: BacklightSource) -> Backend | None:
    """Detect the primary connected xrandr output."""
    out = _run(cmd=["xrandr", "--listmonitors"])
    sysfs = backlight.find()
    brightnessctl = shutil.which("brightnessctl")
    if not out:
        if sysfs is not None and is_wayland_session():
            return Backend(output="", sysfs=sysfs, brightnessctl=brightnessctl)
        return None
    # Format: " 0: +*HDMI-1 1920/480x1080/270+0+0  HDMI-1"
    monitors = [line.split() for line in out.strip().splitlines()[1:]]
    candidates = [parts for parts in monitors if len(parts) >= 4]
    if candidates:
        primary = next(
            (parts for parts in candidates if "*" in parts[1]), candidates[0]
        )
        return Backend(output=primary[-1], sysfs=sysfs, brightnessctl=brightnessctl)
    if sysfs is not None and is_wayland_session():
        return Backend(output="", sysfs=sysfs, brightnessctl=brightnessctl)
    return None


def get_brightness(backend: Backend, backlight: BacklightSource) -> float | None:
    """Read current brightness (0.0-1.0).

    Prefer X11 brightness on X11 sessions and backlight state on Wayland.
    """
    if is_wayland_session():
        if backend.sysfs:
            return backlight.read(path=backend.sysfs)
        return _get_brightness_xrandr(backend=backend)
    brightness = _get_brightness_xrandr(backend=backend)
    if brightness is not None:
        return brightness
    if backend.sysfs:
        return backlight.read(path=backend.sysfs)
    return None


def _get_brightness_xrandr(backend: Backend) -> float | None:
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


def set_brightness(backend: Backend, value: float, backlight: BacklightSource) -> bool:
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
            return backlight.write(path=backend.sysfs, value=clamped)
        return _run(cmd=xrandr_cmd) is not None
    if _run(cmd=xrandr_cmd) is not None:
        return True
    if backend.brightnessctl:
        return (
            _run(cmd=[backend.brightnessctl, "set", f"{round(clamped * 100)}%"])
            is not None
        )
    if backend.sysfs:
        return backlight.write(path=backend.sysfs, value=clamped)
    return False


class NoBacklightSource(BacklightSource):
    def find(self) -> Path | None:
        return None

    def read(self, path: Path) -> float | None:
        return None

    def write(self, path: Path, value: float) -> bool:
        return False


class BrightnessOperations(BrightnessOperationsBase):
    def __init__(self, backlight: BacklightSource) -> None:
        self._backlight = backlight
        self._output = detect_output(backlight)

    def available(self) -> bool:
        return self._output is not None

    def get_brightness(self) -> float | None:
        return (
            get_brightness(backend=self._output, backlight=self._backlight)
            if self._output is not None
            else None
        )

    def set_brightness(self, value: float) -> bool:
        return (
            set_brightness(backend=self._output, value=value, backlight=self._backlight)
            if self._output is not None
            else False
        )
