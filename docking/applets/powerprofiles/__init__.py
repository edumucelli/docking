"""Applet metadata for the Power Profiles applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="powerprofiles",
    name="Power Profiles",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
