"""Applet metadata for the Keyboard Layout applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="keyboardlayout",
    name="Keyboard Layout",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
