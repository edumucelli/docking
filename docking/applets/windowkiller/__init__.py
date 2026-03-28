"""Applet metadata for the Window Killer applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="windowkiller",
    name="Window Killer",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
