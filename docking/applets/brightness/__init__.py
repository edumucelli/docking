"""Applet metadata for the Brightness applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="brightness",
    name="Brightness",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
