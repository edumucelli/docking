"""Public package surface for the Brightness applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``BrightnessApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from .applet import BrightnessApplet
from .state import (
    STEP,
    Backend,
    brightness_icon_name,
    detect_output,
    get_brightness,
    set_brightness,
)

__all__ = [
    "STEP",
    "Backend",
    "BrightnessApplet",
    "brightness_icon_name",
    "detect_output",
    "get_brightness",
    "set_brightness",
]
