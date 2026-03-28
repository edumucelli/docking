"""Applet metadata for the Battery applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="battery",
    name="Battery",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
