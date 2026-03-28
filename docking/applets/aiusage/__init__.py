"""Applet metadata for the AI Usage applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="aiusage",
    name="AI Usage",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
