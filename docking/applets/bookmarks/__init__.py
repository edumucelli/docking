"""Applet metadata for the Bookmarks applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="bookmarks",
    name="Bookmarks",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
