"""Linux volume integration, using PulseAudio or ALSA controls."""

import re
import shutil

from docking.applets.volume.backend import VolumeBackend, VolumeControls
from docking.applets.volume.command import _run
from docking.applets.volume.desktop_settings import SETTINGS
from docking.applets.volume.pulseaudio import BACKEND as PULSEAUDIO
from docking.applets.volume.state import VolumeState

_AMIXER_RE = re.compile(r"\[(\d+)%\].*?\[(on|off)\]")


def _amixer_get_state() -> VolumeState | None:
    out = _run(cmd=["amixer", "get", "Master"], action="get_state")
    if out is None:
        return None
    return _parse_amixer(output=out)


def _amixer_set_volume(volume: int) -> None:
    _run(cmd=["amixer", "set", "Master", f"{volume}%"], action="set_volume")


def _amixer_toggle_mute() -> None:
    _run(cmd=["amixer", "set", "Master", "toggle"], action="toggle_mute")


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
