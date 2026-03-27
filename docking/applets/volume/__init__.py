"""Public package surface for the Volume applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``VolumeApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="volume",
    name="Volume",
    category=AppletCategory.SYSTEM,
)

from .applet import VolumeApplet
from .state import (
    _BACKENDS,
    STEP,
    Backend,
    VolumeState,
    _detect_backend,
    _parse_amixer,
    _parse_pactl_mute,
    _parse_pactl_volume,
    _run,
    _volume_icon_name,
)

__all__ = [
    "STEP",
    "_BACKENDS",
    "Backend",
    "VolumeApplet",
    "VolumeState",
    "_detect_backend",
    "_parse_amixer",
    "_parse_pactl_mute",
    "_parse_pactl_volume",
    "_run",
    "_volume_icon_name",
    "meta",
]
