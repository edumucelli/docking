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

"""Volume state, parsers and shared icon selection."""

from __future__ import annotations

from typing import NamedTuple

STEP = 5


class VolumeState(NamedTuple):
    """Current audio output state."""

    volume: int
    muted: bool


def _volume_icon_name(volume: int, muted: bool) -> str:
    """Map volume level + mute state to a FreeDesktop icon name."""
    if muted or volume == 0:
        return "audio-volume-muted"
    if volume <= 33:
        return "audio-volume-low"
    if volume <= 66:
        return "audio-volume-medium"
    return "audio-volume-high"
