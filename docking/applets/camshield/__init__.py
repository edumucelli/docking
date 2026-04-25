"""Applet metadata for the Cam Shield applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="camshield",
    name="Cam Shield",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
