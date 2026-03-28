"""Applet metadata for the Hydration applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="hydration",
    name="Hydration",
    category=AppletCategory.WELLNESS,
)

__all__ = ["meta"]
