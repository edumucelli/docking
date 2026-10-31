"""Linux volume integration, using PulseAudio or ALSA controls."""

import shutil

from docking.applets.volume.backend import VolumeBackend
from docking.applets.volume.implementations.unix.command import run_command
from docking.applets.volume.implementations.unix.pulseaudio import BACKEND as PULSEAUDIO
from docking.applets.volume.state import VolumeState, _parse_amixer


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
    VolumeBackend("amixer", _amixer_get_state, _amixer_set_volume, _amixer_toggle_mute),
)


def create_backend() -> VolumeBackend | None:
    return next(
        (backend for backend in _BACKENDS if shutil.which(backend.command)), None
    )
