"""Applet metadata for the Sunrise applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="sunrise",
    name="Sunrise",
    category=AppletCategory.INFORMATION,
)

__all__ = ["meta"]
