"""Applet metadata for the Stretch Coach applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="stretchcoach",
    name="Stretch Coach",
    category=AppletCategory.WELLNESS,
)

__all__ = ["meta"]
