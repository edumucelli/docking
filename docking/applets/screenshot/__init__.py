"""Applet metadata for the Screenshot applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="screenshot",
    name="Screenshot",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
