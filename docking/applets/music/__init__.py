"""Music applet package."""

from .applet import MusicApplet
from .artwork import CoverArtResolver
from .render import create_music_icon
from .state import (
    VOLUME_STEP,
    HybridBackend,
    MprisBackend,
    MusicState,
    PlayerctlBackend,
    RhythmboxClientBackend,
    clamp_percent,
    play_pause_menu_label,
    tooltip_text,
    unavailable_state,
)

__all__ = [
    "CoverArtResolver",
    "HybridBackend",
    "MprisBackend",
    "MusicApplet",
    "MusicState",
    "PlayerctlBackend",
    "RhythmboxClientBackend",
    "VOLUME_STEP",
    "clamp_percent",
    "create_music_icon",
    "play_pause_menu_label",
    "tooltip_text",
    "unavailable_state",
]
