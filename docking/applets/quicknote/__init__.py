"""Public surface for the Quick Note applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="quicknote",
    name="Quick Note",
    category=AppletCategory.PRODUCTIVITY,
)

from .applet import QuickNoteApplet

__all__ = ["QuickNoteApplet", "meta"]
