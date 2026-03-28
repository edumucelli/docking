"""Applet metadata for the Unit Converter applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="unitconverter",
    name="Unit Converter",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
