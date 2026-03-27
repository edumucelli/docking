"""Public package surface for the Applications applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``ApplicationsApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="applications",
    name="Applications",
    category=AppletCategory.LAUNCHER,
)

from .applet import ApplicationsApplet
from .state import _build_app_categories

__all__ = ["ApplicationsApplet", "_build_app_categories", "meta"]
