"""Desktop docklet -- toggles show desktop on click.

Uses Wnck to minimize/restore all windows, matching Plank's behavior.
Static icon (user-desktop), no menu items, no timers.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Wnck  # noqa: E402

from docking.docklets.base import Docklet


def _load_theme_icon(name: str, size: int) -> GdkPixbuf.Pixbuf | None:
    """Load an icon by name from the default GTK icon theme."""
    theme = Gtk.IconTheme.get_default()
    try:
        return theme.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
    except GLib.Error:
        return None


class DesktopDocklet(Docklet):
    """Click to toggle showing the desktop (minimize/restore all windows).

    No preferences, no menu items, no timers -- just a click action.
    """

    id = "desktop"
    name = "Desktop"
    icon_name = "user-desktop"

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Return static user-desktop icon from theme."""
        return _load_theme_icon("user-desktop", size)

    def on_clicked(self) -> None:
        """Toggle show desktop via Wnck.

        force_update() ensures Wnck's window list is current before
        querying/toggling the showing-desktop state.
        """
        screen = Wnck.Screen.get_default()
        screen.force_update()
        screen.toggle_showing_desktop(not screen.get_showing_desktop())
