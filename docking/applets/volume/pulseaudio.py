"""PulseAudio volume controls, available on Linux and BSD."""

from docking.applets.volume.backend import VolumeBackend
from docking.applets.volume.command import _run
from docking.applets.volume.state import (
    VolumeState,
    _parse_pactl_mute,
    _parse_pactl_volume,
)


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


BACKEND = VolumeBackend(
    "pactl", _pactl_get_state, _pactl_set_volume, _pactl_toggle_mute
)
