"""Metadata for the persistent Telegram Web applet."""

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(id="telegram", name="Telegram", category=AppletCategory.LAUNCHER)

__all__ = ["meta"]
