"""Applet metadata for the Volume applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="volume",
    name="Volume",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
