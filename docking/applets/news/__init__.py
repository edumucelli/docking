"""Applet metadata for the country-based News RSS applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="news",
    name="News",
    category=AppletCategory.INFORMATION,
)

__all__ = ["meta"]
