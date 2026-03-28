"""Applet metadata for the Notifications applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="notifications",
    name="Notifications",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
