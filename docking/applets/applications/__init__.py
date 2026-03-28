"""Applet metadata for the Applications applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="applications",
    name="Applications",
    category=AppletCategory.LAUNCHER,
)

__all__ = ["meta"]
