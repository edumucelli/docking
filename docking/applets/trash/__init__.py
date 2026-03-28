"""Applet metadata for the Trash applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="trash",
    name="Trash",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
