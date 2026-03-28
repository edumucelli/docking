"""Applet metadata for the Today in History applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="todayinhistory",
    name="Today in History",
    category=AppletCategory.INFORMATION,
)

__all__ = ["meta"]
