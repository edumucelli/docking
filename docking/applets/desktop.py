"""Desktop applet -- toggles show desktop on click.

Uses Wnck to minimize/restore all windows, matching Plank's behavior.
Static icon (user-desktop), no menu items, no timers.
"""

from __future__ import annotations

import gi

gi.require_version("Wnck", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Wnck  # noqa: E402

from docking.applets.base import Applet, load_theme_icon
from docking.applets.identity import AppletId


class DesktopApplet(Applet):
    """Click to toggle showing the desktop (minimize/restore all windows).

    No preferences, no menu items, no timers -- just a click action.
    """

    id = AppletId.DESKTOP
    name = "Desktop"
    icon_name = "user-desktop"

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Return static user-desktop icon from theme."""
        return load_theme_icon(name="user-desktop", size=size)

    def on_clicked(self) -> None:
        """Toggle show desktop via Wnck.

        force_update() ensures Wnck's window list is current before
        querying/toggling the showing-desktop state.
        """
        screen = Wnck.Screen.get_default()
        screen.force_update()
        screen.toggle_showing_desktop(not screen.get_showing_desktop())
