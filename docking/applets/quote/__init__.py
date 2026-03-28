"""Applet metadata for the Quote applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="quote",
    name="Quote",
    category=AppletCategory.INFORMATION,
)

__all__ = ["meta"]
