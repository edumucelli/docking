"""State and backend helpers for volume applet."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from typing import NamedTuple

from docking.applets.identity import AppletId
from docking.log import get_logger, with_context

_log = with_context(get_logger(name="volume"), applet_id=str(AppletId.VOLUME))

STEP = 5


class VolumeState(NamedTuple):
    """Current audio output state."""

    volume: int
    muted: bool


_PACTL_VOL_RE = re.compile(r"(\d+)%")
_AMIXER_RE = re.compile(r"\[(\d+)%\].*?\[(on|off)\]")


def _parse_pactl_volume(output: str) -> int | None:
    """Extract first percentage from pactl get-sink-volume output."""
    m = _PACTL_VOL_RE.search(output)
    return int(m.group(1)) if m else None


def _parse_pactl_mute(output: str) -> bool | None:
    """Parse 'Mute: yes/no' from pactl get-sink-mute output."""
    if "yes" in output.lower():
        return True
    if "no" in output.lower():
        return False
    return None


def _parse_amixer(output: str) -> VolumeState | None:
    """Extract volume % and on/off from amixer get Master output."""
    m = _AMIXER_RE.search(output)
    if not m:
        return None
    return VolumeState(volume=int(m.group(1)), muted=m.group(2) == "off")


def _volume_icon_name(volume: int, muted: bool) -> str:
    """Map volume level + mute state to a FreeDesktop icon name."""
    if muted or volume == 0:
        return "audio-volume-muted"
    if volume <= 33:
        return "audio-volume-low"
    if volume <= 66:
        return "audio-volume-medium"
    return "audio-volume-high"


class Backend(NamedTuple):
    """Audio backend with commands for reading/setting volume."""

    command: str
    get_state: Callable[[], VolumeState | None]
    set_volume: Callable[[int], None]
    toggle_mute: Callable[[], None]


def _run(cmd: list[str], action: str) -> str | None:
    """Run command, return stdout or None on failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            return result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.bind(action=action).warning(f"Failed to run {cmd}: {exc}")
    return None


def _pactl_get_state() -> VolumeState | None:
    vol_out = _run(
        cmd=["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
        action="get_state",
    )
    mute_out = _run(
        cmd=["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
        action="get_state",
    )
    if vol_out is None or mute_out is None:
        return None
    vol = _parse_pactl_volume(output=vol_out)
    muted = _parse_pactl_mute(output=mute_out)
    if vol is None or muted is None:
        return None
    return VolumeState(volume=vol, muted=muted)


def _pactl_set_volume(volume: int) -> None:
    _run(
        cmd=["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%"],
        action="set_volume",
    )


def _pactl_toggle_mute() -> None:
    _run(
        cmd=["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
        action="toggle_mute",
    )


def _amixer_get_state() -> VolumeState | None:
    out = _run(cmd=["amixer", "get", "Master"], action="get_state")
    if out is None:
        return None
    return _parse_amixer(output=out)


def _amixer_set_volume(volume: int) -> None:
    _run(cmd=["amixer", "set", "Master", f"{volume}%"], action="set_volume")


def _amixer_toggle_mute() -> None:
    _run(cmd=["amixer", "set", "Master", "toggle"], action="toggle_mute")


_BACKENDS: tuple[Backend, ...] = (
    Backend(
        command="pactl",
        get_state=_pactl_get_state,
        set_volume=_pactl_set_volume,
        toggle_mute=_pactl_toggle_mute,
    ),
    Backend(
        command="amixer",
        get_state=_amixer_get_state,
        set_volume=_amixer_set_volume,
        toggle_mute=_amixer_toggle_mute,
    ),
)


def _detect_backend() -> Backend | None:
    """Return the first available audio backend, or None."""
    for backend in _BACKENDS:
        if shutil.which(backend.command):
            return backend
    return None
