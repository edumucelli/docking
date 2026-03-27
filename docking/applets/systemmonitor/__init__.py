"""Public package surface for the System Monitor applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``SystemMonitorApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="systemmonitor",
    name="System Monitor",
    category=AppletCategory.SYSTEM,
)

from .applet import SystemMonitorApplet
from .state import (
    CpuSample,
    cpu_hue_rgb,
    cpu_percent,
    parse_proc_meminfo,
    parse_proc_stat,
)

__all__ = [
    "CpuSample",
    "SystemMonitorApplet",
    "cpu_hue_rgb",
    "cpu_percent",
    "meta",
    "parse_proc_meminfo",
    "parse_proc_stat",
]
