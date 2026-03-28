"""Applet metadata for the Clock applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="clock",
    name="Clock",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
