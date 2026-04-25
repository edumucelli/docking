"""Applet metadata for the Certwatch applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="certwatch",
    name="Cert Watch",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
