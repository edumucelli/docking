"""Applet metadata for the Random Trivia applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="trivia",
    name="Random Trivia",
    category=AppletCategory.INFORMATION,
)

__all__ = ["meta"]
