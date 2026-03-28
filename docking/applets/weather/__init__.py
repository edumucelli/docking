"""Applet metadata for the Weather applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="weather",
    name="Weather",
    category=AppletCategory.INFORMATION,
)

__all__ = ["meta"]
