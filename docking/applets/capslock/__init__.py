"""Applet metadata for the Caps Lock applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="capslock",
    name="Caps Lock",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
