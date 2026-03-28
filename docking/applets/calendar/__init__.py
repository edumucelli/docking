"""Applet metadata for the Calendar applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="calendar",
    name="Calendar",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
