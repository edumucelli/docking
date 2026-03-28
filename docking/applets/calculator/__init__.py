"""Applet metadata for the Calculator applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="calculator",
    name="Calculator",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
