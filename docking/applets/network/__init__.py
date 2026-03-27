"""Public package surface for the Network applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``NetworkApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="network",
    name="Network",
    category=AppletCategory.SYSTEM,
)

from .applet import NM, GLib, Gtk, NetworkApplet, time  # noqa: F401
from .state import (
    TrafficCounters,
    TrafficSpeeds,
    compute_speeds,
    format_speed,
    parse_proc_net_dev,
    signal_to_icon,
)

__all__ = [
    "NetworkApplet",
    "TrafficCounters",
    "TrafficSpeeds",
    "compute_speeds",
    "format_speed",
    "meta",
    "parse_proc_net_dev",
    "signal_to_icon",
    "time",
]
