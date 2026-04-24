"""Applet metadata for the Desk Presence applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="deskpresence",
    name="Desk Presence",
    category=AppletCategory.WELLNESS,
)

__all__ = ["meta"]
