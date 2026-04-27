"""Applet metadata for the Drag Share applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="dragshare",
    name="Drag Share",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
