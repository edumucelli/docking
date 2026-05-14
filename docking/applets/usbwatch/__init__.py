"""Applet metadata for the USB Watch applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="usbwatch",
    name="USB Watch",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
