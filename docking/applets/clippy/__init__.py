"""Applet metadata for the Clippy applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="clippy",
    name="Clippy",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
