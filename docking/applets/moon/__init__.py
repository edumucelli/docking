"""Public package surface for the Moon applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``MoonApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from .applet import MoonApplet
from .offline import fetch_moon_offline, illumination_from_phase, moon_phase_from_date
from .state import MoonData, fetch_moon, phase_name

__all__ = [
    "MoonApplet",
    "MoonData",
    "fetch_moon",
    "fetch_moon_offline",
    "illumination_from_phase",
    "moon_phase_from_date",
    "phase_name",
]
