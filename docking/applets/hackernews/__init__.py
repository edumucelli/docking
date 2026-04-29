"""Applet metadata for the Hacker News applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="hackernews",
    name="Hacker News",
    category=AppletCategory.INFORMATION,
)

__all__ = ["meta"]
