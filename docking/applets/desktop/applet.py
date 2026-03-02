"""GTK lifecycle glue for Desktop applet."""

from __future__ import annotations

import gi

gi.require_version("Wnck", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Wnck  # noqa: E402

from docking.applets.base import Applet
from docking.applets.desktop.render import create_icon
from docking.applets.desktop.state import next_showing_desktop
from docking.applets.identity import AppletId


class DesktopApplet(Applet):
    """Click to toggle showing the desktop (minimize/restore all windows)."""

    id = AppletId.DESKTOP
    name = "Desktop"
    icon_name = "user-desktop"

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(size=size)

    def on_clicked(self) -> None:
        """Toggle show desktop via Wnck."""
        screen = Wnck.Screen.get_default()
        screen.force_update()
        screen.toggle_showing_desktop(
            next_showing_desktop(current=screen.get_showing_desktop())
        )
