"""Applet metadata for the Desktop applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="desktop",
    name="Desktop",
    category=AppletCategory.LAUNCHER,
)

__all__ = ["meta"]
