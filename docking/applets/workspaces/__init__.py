"""Applet metadata for the Workspaces applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="workspaces",
    name="Workspaces",
    category=AppletCategory.LAUNCHER,
)

__all__ = ["meta"]
