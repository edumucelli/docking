"""Public package surface for the Separator applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``SeparatorApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="separator",
    name="Separator",
    category=AppletCategory.OTHER,
)

from .applet import SeparatorApplet
from .state import DEFAULT_SIZE, MAX_SIZE, MIN_SIZE, STEP, STYLE_LINE, STYLE_SPACE

__all__ = [
    "DEFAULT_SIZE",
    "MAX_SIZE",
    "MIN_SIZE",
    "STEP",
    "STYLE_LINE",
    "STYLE_SPACE",
    "SeparatorApplet",
    "meta",
]
