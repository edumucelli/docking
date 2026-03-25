"""Public package surface for the Ambient applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``AmbientApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from .applet import AmbientApplet
from .state import (
    ALL_SOUNDS,
    DEFAULT_SOUND,
    DEFAULT_VOLUME,
    VOLUME_STEP,
)

__all__ = [
    "ALL_SOUNDS",
    "DEFAULT_SOUND",
    "DEFAULT_VOLUME",
    "VOLUME_STEP",
    "AmbientApplet",
]
