"""Metadata for the optional KDE Connect phone bridge."""

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="kdeconnect",
    name="KDE Connect",
    category=AppletCategory.SYSTEM,
    unsupported_platforms=("win32",),
)

__all__ = ["meta"]
