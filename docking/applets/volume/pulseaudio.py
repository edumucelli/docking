"""PulseAudio volume controls, available on Linux and BSD."""

import re

from docking.applets.volume.backend import VolumeControls
from docking.applets.volume.command import _run
from docking.applets.volume.state import VolumeState

_PACTL_VOL_RE = re.compile(r"(\d+)%")


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


BACKEND = VolumeControls(
    "pactl", _pactl_get_state, _pactl_set_volume, _pactl_toggle_mute
)


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
