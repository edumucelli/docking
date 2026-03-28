"""Applet metadata for the Session applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="session",
    name="Session",
    category=AppletCategory.SYSTEM,
)

__all__ = ["meta"]
