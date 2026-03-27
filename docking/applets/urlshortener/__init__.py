"""Public package surface for the Url Shortener applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``UrlShortenerApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="urlshortener",
    name="URL Shortener",
    category=AppletCategory.PRODUCTIVITY,
)

from .applet import UrlShortenerApplet

__all__ = ["UrlShortenerApplet", "meta"]
