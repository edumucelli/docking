"""Public surface for the Pet applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="pet",
    name="Pet",
    category=AppletCategory.WELLNESS,
)

from .applet import PetApplet

__all__ = ["PetApplet", "meta"]
