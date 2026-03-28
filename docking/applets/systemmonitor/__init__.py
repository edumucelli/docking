"""Applet metadata for the System Monitor applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="systemmonitor",
    name="System Monitor",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
