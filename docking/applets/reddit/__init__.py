"""Applet metadata for the Reddit RSS applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="reddit",
    name="Reddit",
    category=AppletCategory.INFORMATION,
)

__all__ = ["meta"]
