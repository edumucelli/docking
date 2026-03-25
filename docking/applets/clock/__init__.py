"""Public package surface for the Clock applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``ClockApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from .applet import ClockApplet
from .state import (
    hour_rotation_12h,
    hour_rotation_24h,
    minute_rotation,
)

__all__ = [
    "ClockApplet",
    "hour_rotation_12h",
    "hour_rotation_24h",
    "minute_rotation",
]
