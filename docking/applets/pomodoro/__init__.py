"""Applet metadata for the Pomodoro applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="pomodoro",
    name="Pomodoro",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
