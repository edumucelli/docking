"""Applet metadata for the Ambient applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="ambient",
    name="Ambient",
    category=AppletCategory.WELLNESS,
)

__all__ = ["meta"]
