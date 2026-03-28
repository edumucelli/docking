"""Applet metadata for the Music applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="music",
    name="Music",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
