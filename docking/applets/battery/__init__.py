"""Public package surface for the Battery applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``BatteryApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from .applet import BatteryApplet
from .state import (
    BatteryState,
    read_battery,
    resolve_battery_icon,
)

__all__ = ["BatteryApplet", "BatteryState", "read_battery", "resolve_battery_icon"]
