"""Applet metadata for Plant Care."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="plantcare",
    name="Plant Care",
    category=AppletCategory.WELLNESS,
)

__all__ = ["meta"]
