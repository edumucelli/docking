"""Applet metadata for the Color Picker applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="colorpicker",
    name="Color Picker",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
