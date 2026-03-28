"""Applet metadata for the Bluetooth applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="bluetooth",
    name="Bluetooth",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
