"""Applet metadata for the Gmail applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="gmail",
    name="Gmail",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
