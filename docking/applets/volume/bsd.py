"""Native FreeBSD mixer and OpenBSD sndio controls for the Volume applet."""

from __future__ import annotations

import re
import shutil
import sys

from docking.applets.volume.state import Backend, VolumeState, _run


def _number(text: str | None) -> float | None:
    if text is None:
        return None
    # mixer -o prints key=value; sndioctl -n prints just the value.
    value = text.strip().split("=", 1)[-1]
    parts = value.split(":")
    try:
        return sum(float(p) for p in parts) / len(parts)
    except ValueError:
        return None


def backend() -> Backend | None:
    openbsd = sys.platform.startswith("openbsd")
    tool = "sndioctl" if openbsd else "mixer"
    if shutil.which(tool) is None:
        return None
    prefix = [tool, "-n"] if openbsd else [tool, "-o"]
    volume_key = "output.level" if openbsd else "vol.volume"
    mute_key = "output.mute" if openbsd else "vol.mute"

    def get_state() -> VolumeState | None:
        volume = _number(_run([*prefix, volume_key], "get_state"))
        mute = _run([*prefix, mute_key], "get_state")
        if volume is None or mute is None:
            return None
        raw_mute = mute.strip().split("=", 1)[-1]
        if not re.fullmatch(r"(?:0|1|on|off)", raw_mute):
            return None
        return VolumeState(
            round(max(0.0, min(1.0, volume)) * 100), raw_mute in {"1", "on"}
        )

    def set_volume(value: int) -> None:
        _run([tool, f"{volume_key}={max(0, min(100, value)) / 100:.2f}"], "set_volume")

    def toggle_mute() -> None:
        _run([tool, f"{mute_key}={'!' if openbsd else 'toggle'}"], "toggle_mute")

    return Backend(tool, get_state, set_volume, toggle_mute)
