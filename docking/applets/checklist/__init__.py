"""Applet metadata for Checklist."""

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="checklist",
    name="Checklist",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
