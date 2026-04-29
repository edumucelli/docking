"""Applet metadata for the Thermals applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="thermals",
    name="Thermals",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
