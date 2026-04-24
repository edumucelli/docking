"""Applet metadata for the Speedtest applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="speedtest",
    name="Speedtest",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
