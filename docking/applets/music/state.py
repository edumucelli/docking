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


"""Platform-neutral media session values and presentation helpers."""

from __future__ import annotations

from dataclasses import dataclass

from docking.applets.tooltip import structured_tooltip
from docking.i18n import _

VOLUME_STEP = 5


@dataclass(frozen=True, slots=True)
class MusicState:
    """Current selected media player state."""

    available: bool = False
    player_name: str = ""
    player_icon_name: str = ""
    player_application_id: str = ""
    player_id: str = ""
    playback_status: str = "Stopped"
    title: str = ""
    artist: str = ""
    album: str = ""
    volume_percent: int = 0
    can_set_volume: bool = False
    volume_id: str = ""
    art_data: bytes = b""
    can_play_pause: bool = False
    can_go_next: bool = False
    can_go_previous: bool = False
    art_url: str = ""
    track_url: str = ""


def has_active_media(state: MusicState) -> bool:
    """Return whether a player exposes a usable current media session."""
    if not state.available:
        return False
    return (
        state.playback_status in {"Playing", "Paused"}
        or bool(state.title or state.artist or state.album or state.track_url)
        or state.can_play_pause
    )


def clamp_percent(value: int) -> int:
    """Clamp integer percent to [0, 100]."""
    return max(0, min(100, int(value)))


def unavailable_state() -> MusicState:
    """Canonical unavailable state."""
    return MusicState()


def play_pause_menu_label(state: MusicState) -> str:
    """Menu label for transport toggle."""
    return _("Pause") if state.playback_status == "Playing" else _("Play")


def tooltip_text(state: MusicState) -> str:
    """Detailed tooltip text for the music applet."""
    if not state.available:
        return structured_tooltip(
            title=_("Music"),
            primary=_("No active player"),
        )

    details = " - ".join(part for part in [state.artist, state.title] if part)
    primary = None
    if details:
        primary = details
    elif state.title:
        primary = state.title
    secondary = []
    if state.album:
        secondary.append(f"Album: {state.album}")
    return structured_tooltip(
        title=_("Music"),
        primary=primary,
        details=secondary,
    )
