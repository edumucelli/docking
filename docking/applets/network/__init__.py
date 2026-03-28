"""Applet metadata for the Network applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="network",
    name="Network",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
