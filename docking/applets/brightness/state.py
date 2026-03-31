"""Pure state helpers for Brightness applet -- no GTK dependency."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from docking.log import get_logger

log = get_logger(name="brightness.state")

_BACKLIGHT_DIR = Path("/sys/class/backlight")


class Backend(NamedTuple):
    """A brightness backend with its xrandr output and optional sysfs path."""

    output: str  # xrandr output name (e.g. "eDP-1")
    sysfs: Path | None = None  # e.g. /sys/class/backlight/intel_backlight


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


def detect_output() -> Backend | None:
    """Detect the primary connected xrandr output."""
    out = _run(cmd=["xrandr", "--listmonitors"])
    if not out:
        return None
    # Format: " 0: +*HDMI-1 1920/480x1080/270+0+0  HDMI-1"
    for line in out.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            return Backend(output=parts[-1], sysfs=_find_sysfs_backlight())
    return None


def get_brightness(backend: Backend) -> float | None:
    """Read current brightness (0.0-1.0).

    Prefers sysfs (instant, no X11 lock) over xrandr --verbose (slow).
    """
    if backend.sysfs:
        return _get_brightness_sysfs(path=backend.sysfs)
    return _get_brightness_xrandr(backend=backend)


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


def _get_brightness_xrandr(backend: Backend) -> float | None:
    """Read brightness via xrandr --verbose (fallback, slow)."""
    out = _run(cmd=["xrandr", "--verbose"])
    if not out:
        return None
    in_output = False
    for line in out.splitlines():
        if line and not line[0].isspace() and backend.output in line:
            in_output = True
        elif line and not line[0].isspace():
            in_output = False
        if in_output:
            m = re.search(r"Brightness:\s+([\d.]+)", line)
            if m:
                return float(m.group(1))
    return None


def set_brightness(backend: Backend, value: float) -> None:
    """Set brightness (0.1-1.0) via xrandr."""
    clamped = max(0.1, min(1.0, value))
    _run(cmd=["xrandr", "--output", backend.output, "--brightness", f"{clamped:.2f}"])


def brightness_icon_name(brightness: float) -> str:
    """Map brightness level to FreeDesktop icon name."""
    if brightness <= 0.3:
        return "display-brightness-low-symbolic"
    if brightness <= 0.7:
        return "display-brightness-medium-symbolic"
    return "display-brightness-symbolic"


STEP = 0.02
