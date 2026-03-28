"""Applet metadata for the Pet applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="pet",
    name="Pet",
    category=AppletCategory.WELLNESS,
)

__all__ = ["meta"]
