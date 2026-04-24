"""Applet metadata for the Astronomy Picture of the Day applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="apod",
    name="Astronomy Picture of the Day",
    category=AppletCategory.INFORMATION,
)

__all__ = ["meta"]
