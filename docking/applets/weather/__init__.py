"""Weather applet public API and compatibility re-exports."""

from __future__ import annotations

import threading as threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib as GLib

from .api import fetch_air_quality as fetch_air_quality
from .api import fetch_weather as fetch_weather
from .applet import WeatherApplet

__all__ = ["WeatherApplet"]
