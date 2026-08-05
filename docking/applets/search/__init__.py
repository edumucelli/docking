"""Applet metadata for the global Search launcher."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="search",
    name="Search",
    category=AppletCategory.LAUNCHER,
)

__all__ = ["meta"]
