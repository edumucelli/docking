"""Linux volume integration, using PulseAudio or ALSA controls."""

import re
import shutil

_AMIXER_RE = re.compile(r"\[(\d+)%\].*?\[(on|off)\]")

from docking.applets.volume.backend import VolumeBackend, VolumeControls
from docking.applets.volume.implementations.unix.command import run_command
from docking.applets.volume.implementations.unix.desktop_settings import SETTINGS
from docking.applets.volume.implementations.unix.pulseaudio import BACKEND as PULSEAUDIO
from docking.applets.volume.state import VolumeState


def _amixer_get_state() -> VolumeState | None:
    out = run_command(cmd=["amixer", "get", "Master"], action="get_state")
    if out is None:
        return None
    return _parse_amixer(output=out)


def _amixer_set_volume(volume: int) -> None:
    run_command(cmd=["amixer", "set", "Master", f"{volume}%"], action="set_volume")


def _amixer_toggle_mute() -> None:
    run_command(cmd=["amixer", "set", "Master", "toggle"], action="toggle_mute")


_BACKENDS = (
    PULSEAUDIO,
    VolumeControls(
        "amixer", _amixer_get_state, _amixer_set_volume, _amixer_toggle_mute
    ),
)


def create_backend() -> VolumeBackend:
    controls = next(
        (backend for backend in _BACKENDS if shutil.which(backend.command)), None
    )
    return VolumeBackend(controls, SETTINGS)


def _parse_amixer(output: str) -> VolumeState | None:
    """Extract volume % and on/off from amixer get Master output."""
    m = _AMIXER_RE.search(output)
    if not m:
        return None
    return VolumeState(volume=int(m.group(1)), muted=m.group(2) == "off")
