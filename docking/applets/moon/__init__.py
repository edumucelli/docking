"""Applet metadata for the Moon applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="moon",
    name="Moon",
    category=AppletCategory.INFORMATION,
)

__all__ = ["meta"]
