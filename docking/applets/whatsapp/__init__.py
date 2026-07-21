"""Applet metadata for the WhatsApp Web applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="whatsapp",
    name="WhatsApp",
    category=AppletCategory.LAUNCHER,
)

__all__ = ["meta"]
