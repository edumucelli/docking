"""Public package surface for the Weather applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``WeatherApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="weather",
    name="Weather",
    category=AppletCategory.INFORMATION,
)

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib as GLib

from .api import fetch_air_quality as fetch_air_quality
from .api import fetch_weather as fetch_weather
from .applet import WeatherApplet

__all__ = ["WeatherApplet", "meta"]
