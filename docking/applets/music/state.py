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

"""State and backend helpers for music control applet."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from docking.applets.music import meta
from docking.applets.tooltip import structured_tooltip
from docking.i18n import _
from docking.log import get_logger, with_context

log = with_context(get_logger(name="music"), applet_id=meta.id)

VOLUME_STEP = 5

_MPRIS_PREFIX = "org.mpris.MediaPlayer2."
_MPRIS_OBJECT_PATH = "/org/mpris/MediaPlayer2"
_MPRIS_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
_MPRIS_ROOT_IFACE = "org.mpris.MediaPlayer2"
_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
_RB_SERVICE = "org.gnome.Rhythmbox3"
_RB_MPRIS_SERVICE = f"{_MPRIS_PREFIX}rhythmbox"
_RB_VOLUME_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
_WEBKIT_MPRIS_MARKER = "org.webkit.app-"


@dataclass(frozen=True, slots=True)
class MusicState:
    """Current selected media player state."""

    available: bool = False
    player_name: str = ""
    player_icon_name: str = ""
    player_desktop_entry: str = ""
    player_bus_name: str = ""
    playback_status: str = "Stopped"
    title: str = ""
    artist: str = ""
    album: str = ""
    volume_percent: int = 0
    can_play_pause: bool = False
    can_go_next: bool = False
    can_go_previous: bool = False
    art_url: str = ""
    track_url: str = ""


def _is_internal_webkit_player(state: MusicState) -> bool:
    """Return whether an embedded Docking WebKit view exported this player."""
    return (
        state.player_name.casefold() == "docking"
        and _WEBKIT_MPRIS_MARKER in state.player_bus_name.casefold()
    )


def has_active_media(state: MusicState) -> bool:
    """Return whether a player exposes a usable current media session."""
    if not state.available or _is_internal_webkit_player(state):
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


def _normalize_playback_status(raw: str) -> str:
    """Return the canonical playback token for a backend status string.

    The token stays untranslated: it is a state value that several callers
    compare against these literals, which translating it broke.
    """
    value = raw.strip().lower()
    if value == "playing":
        return "Playing"
    if value == "paused":
        return "Paused"
    if value == "stopped":
        return "Stopped"
    if value == "unknown":
        return "Unknown"
    return raw.strip() or "Unknown"


def _normalize_desktop_entry(raw: str) -> str:
    """Normalize desktop-entry-like identifiers to GTK icon names."""
    value = raw.strip()
    if not value:
        return ""

    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    if value.lower().endswith(".desktop"):
        value = value[: -len(".desktop")]
    if value.startswith(_MPRIS_PREFIX):
        value = value[len(_MPRIS_PREFIX) :]

    lowered = value.lower()
    if lowered in {"org.gnome.rhythmbox3", "rhythmbox3"}:
        return "rhythmbox"

    if "." in value:
        if lowered.startswith(("org.", "com.", "net.", "io.")):
            value = value.rsplit(".", 1)[-1]
        else:
            value = value.split(".", 1)[0]

    value = value.strip().lower()
    if value == "rhythmbox3":
        return "rhythmbox"
    return value


def _icon_name_from_bus_name(bus_name: str) -> str:
    if not bus_name:
        return ""
    tail = (
        bus_name[len(_MPRIS_PREFIX) :]
        if bus_name.startswith(_MPRIS_PREFIX)
        else bus_name
    )
    return _normalize_desktop_entry(tail)


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


def _unpack(value: Any) -> Any:
    if hasattr(value, "unpack"):
        try:
            return value.unpack()
        except Exception as exc:
            log.debug("Failed to unpack GLib value %r: %s", value, exc)
            return value
    return value


def _as_str(value: Any) -> str:
    value = _unpack(value)
    if value is None:
        return ""
    return str(value)


def _as_bool(value: Any, default: bool = False) -> bool:
    value = _unpack(value)
    if isinstance(value, bool):
        return value
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    value = _unpack(value)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        log.debug("Failed to coerce music value %r to float: %s", value, exc)
        return default


def _normalize_volume_percent(value: float) -> int:
    """Normalize volume reported as ratio (0..1) or percent (0..100)."""
    # PulseAudio/MPRIS reports 0.0-1.0, ALSA reports 0-100; 1.5 threshold
    # distinguishes the two (no sane volume is between 1.5% and 150%).
    if value <= 1.5:
        return clamp_percent(round(value * 100))
    return clamp_percent(round(value))


def _metadata_str(metadata: dict[str, Any], key: str) -> str:
    return _as_str(metadata.get(key, ""))


def _metadata_artist(metadata: dict[str, Any]) -> str:
    value = _unpack(metadata.get("xesam:artist", []))
    if isinstance(value, list | tuple):
        return str(value[0]) if value else ""
    return _as_str(value)
