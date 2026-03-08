"""Pure state and metadata logic for Ambient applet."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, NamedTuple

from docking.i18n import _

VOLUME_STEP = 0.1


class Sound(NamedTuple):
    """An ambient sound entry."""

    name: str
    label: str
    kind: str  # "file" or "noise"


# Bundled OGG files (CC0 / Public Domain)
FILE_SOUNDS: list[Sound] = [
    Sound(name="birds", label=_("Birds"), kind="file"),
    Sound(name="boat", label=_("Boat"), kind="file"),
    Sound(name="coffee-shop", label=_("Coffee Shop"), kind="file"),
    Sound(name="fireplace", label=_("Fireplace"), kind="file"),
    Sound(name="stream", label=_("Stream"), kind="file"),
    Sound(name="summer-night", label=_("Summer Night"), kind="file"),
    Sound(name="wind", label=_("Wind"), kind="file"),
]

# Procedural noise via GStreamer audiotestsrc
NOISE_SOUNDS: list[Sound] = [
    Sound(name="white-noise", label=_("White Noise"), kind="noise"),
    Sound(name="pink-noise", label=_("Pink Noise"), kind="noise"),
]

ALL_SOUNDS: list[Sound] = FILE_SOUNDS + NOISE_SOUNDS

NOISE_WAVES: dict[str, int] = {
    "white-noise": 0,  # audiotestsrc wave=white-noise
    "pink-noise": 6,  # audiotestsrc wave=pink-noise (tpd)
}

DEFAULT_SOUND = "birds"
DEFAULT_VOLUME = 0.5


@dataclass(frozen=True, slots=True)
class AmbientState:
    """State for Ambient behavior."""

    current: str = DEFAULT_SOUND
    volume: float = DEFAULT_VOLUME
    playing: bool = False


def state_from_prefs(prefs: Mapping[str, Any] | None) -> AmbientState:
    """Build state from persisted preferences."""
    if not prefs:
        return AmbientState()
    return AmbientState(
        current=str(prefs.get("sound", DEFAULT_SOUND)),
        volume=float(prefs.get("volume", DEFAULT_VOLUME)),
        playing=False,
    )


def prefs_from_state(state: AmbientState) -> dict[str, object]:
    """Return preferences payload to persist."""
    return {"sound": state.current, "volume": state.volume}


def sound_label(name: str) -> str:
    """Resolve sound label by name."""
    return next((sound.label for sound in ALL_SOUNDS if sound.name == name), name)


def tooltip_text(state: AmbientState) -> str:
    """Build tooltip string for current state."""
    if state.playing:
        vol_pct = int(state.volume * 100)
        return _("Playing: {sound} ({pct}%)").format(
            sound=sound_label(name=state.current), pct=vol_pct
        )
    return _("Ambient")


def set_playing(state: AmbientState, playing: bool) -> AmbientState:
    """Set current playback status."""
    return replace(state, playing=playing)


def set_sound(state: AmbientState, name: str) -> AmbientState:
    """Set selected sound."""
    return replace(state, current=name)


def adjust_volume(state: AmbientState, direction_up: bool) -> AmbientState:
    """Adjust volume with clamp [0, 1]."""
    if direction_up:
        value = min(1.0, state.volume + VOLUME_STEP)
    else:
        value = max(0.0, state.volume - VOLUME_STEP)
    return replace(state, volume=value)
