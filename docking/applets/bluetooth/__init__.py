"""Public package surface for the Bluetooth applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``BluetoothApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="bluetooth",
    name="Bluetooth",
    category=AppletCategory.SYSTEM,
)

from .applet import BluetoothApplet
from .render import create_bluetooth_icon
from .state import (
    BluetoothAdapterState,
    BluetoothDeviceState,
    BluetoothState,
    BluezBackend,
    adapter_from_state,
    build_tooltip,
    connected_count,
    device_menu_label,
    unavailable_state,
)

__all__ = [
    "BluetoothAdapterState",
    "BluetoothApplet",
    "BluetoothDeviceState",
    "BluetoothState",
    "BluezBackend",
    "adapter_from_state",
    "build_tooltip",
    "connected_count",
    "create_bluetooth_icon",
    "device_menu_label",
    "meta",
    "unavailable_state",
]
