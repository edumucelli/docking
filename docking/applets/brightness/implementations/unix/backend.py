"""Unix native operations extracted without changing fallback policy."""

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

from ...backend import BacklightSource

log = get_logger("brightness")


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


class UnixBrightnessBackend(BrightnessBackend):
    def __init__(self, backlight: BacklightSource) -> None:
        self._backlight = backlight
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._last_state = BrightnessState()

    def _read(self) -> tuple[Backend | None, BrightnessState]:
        if self._stopped.is_set():
            return None, BrightnessState()
        display = detect_output(self._backlight)
        if display is None:
            self._last_state = BrightnessState()
            return None, self._last_state
        level = get_brightness(display, self._backlight)
        target = f"{display.output}|{display.sysfs or ''}"
        state = BrightnessState(level=level, target=target)
        if level is None and target == self._last_state.target:
            # A transient command/read failure must not discard the last
            # measured level for this display, as before the extraction.
            state = self._last_state
        self._last_state = state
        return display, state

    def get_state(self) -> BrightnessState:
        with self._lock:
            previous = self._last_state
            state = self._read()[1]
            if (
                state.available
                and previous.available
                and state.target == previous.target
                and state.level is not None
                and previous.level is not None
                and abs(state.level - previous.level) < 0.01
            ):
                self._last_state = previous
                return previous
            return state

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
                set_brightness(display, value, self._backlight)
            return self._read()[1]

    def stop(self) -> None:
        self._stopped.set()
