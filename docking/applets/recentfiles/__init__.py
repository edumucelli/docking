"""Applet metadata for the Recent Files applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="recentfiles",
    name="Recent Files",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
