"""Public package surface for the Trash applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``TrashApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="trash",
    name="Trash",
    category=AppletCategory.SYSTEM,
)

from .applet import TrashApplet
from .state import _count_trash_items

__all__ = [
    "TrashApplet",
    "_count_trash_items",
    "meta",
]
