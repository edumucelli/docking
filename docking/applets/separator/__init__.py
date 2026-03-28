"""Applet metadata for the Separator applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="separator",
    name="Separator",
    category=AppletCategory.OTHER,
)

__all__ = ["meta"]
