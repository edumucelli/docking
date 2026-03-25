"""Public package surface for the Music applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``MusicApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

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
    "VOLUME_STEP",
    "CoverArtResolver",
    "HybridBackend",
    "MprisBackend",
    "MusicApplet",
    "MusicState",
    "PlayerctlBackend",
    "RhythmboxClientBackend",
    "clamp_percent",
    "create_music_icon",
    "play_pause_menu_label",
    "tooltip_text",
    "unavailable_state",
]
