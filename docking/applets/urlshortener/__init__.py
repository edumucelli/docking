"""Applet metadata for the URL Shortener applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="urlshortener",
    name="URL Shortener",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
