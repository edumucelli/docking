"""Metadata for the optional Gmail unread-count applet."""

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(id="gmail", name="Gmail", category=AppletCategory.PRODUCTIVITY)

__all__ = ["meta"]
