# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""State and backend helpers for volume applet."""

from __future__ import annotations

import re
from typing import NamedTuple

from docking.applets.volume import meta
from docking.log import get_logger, with_context

log = with_context(get_logger(name="volume"), applet_id=meta.id)

STEP = 5


class VolumeState(NamedTuple):
    """Current audio output state."""

    volume: int
    muted: bool


_PACTL_VOL_RE = re.compile(r"(\d+)%")
_AMIXER_RE = re.compile(r"\[(\d+)%\].*?\[(on|off)\]")
_VOLUME_SETTINGS_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("gnome-control-center", "sound"),
    ("mate-volume-control",),
    ("xfce4-mixer-settings",),
    ("pavucontrol",),
    ("kcmshell6", "kcm_pulseaudio"),
    ("kcmshell5", "kcm_pulseaudio"),
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
