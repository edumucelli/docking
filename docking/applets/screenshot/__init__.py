"""Public package surface for the Screenshot applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``ScreenshotApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="screenshot",
    name="Screenshot",
    category=AppletCategory.SYSTEM,
)

from .applet import ScreenshotApplet
from .state import _TOOLS, Tool, _detect_tool, _run

__all__ = [
    "_TOOLS",
    "ScreenshotApplet",
    "Tool",
    "_detect_tool",
    "_run",
    "meta",
]
