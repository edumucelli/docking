"""Rendering and menu item UI helpers for Applications applet."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, Gtk  # noqa: E402

from docking.applets.base import load_theme_icon

MENU_ICON_PX = 16


def create_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Static app grid icon."""
    return load_theme_icon(name="view-app-grid", size=size) or load_theme_icon(
        name="gnome-applications",
        size=size,
    )


def normalize_menu_icon(image: Gtk.Image) -> None:
    """Force consistent menu icon size across themes/environments."""
    image.set_pixel_size(MENU_ICON_PX)
    image.set_size_request(MENU_ICON_PX, MENU_ICON_PX)
    image.set_valign(Gtk.Align.CENTER)


def make_menu_item_with_icon(
    label: str,
    icon_name: str | None = None,
    gicon: Gio.Icon | None = None,
) -> Gtk.MenuItem:
    """Create a Gtk.MenuItem with an optional icon using non-deprecated widgets."""
    item = Gtk.MenuItem()
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row.set_halign(Gtk.Align.START)
    row.set_margin_start(0)
    row.set_margin_end(0)

    image: Gtk.Image | None = None
    if gicon is not None:
        image = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.MENU)
    elif icon_name:
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)

    if image is not None:
        normalize_menu_icon(image=image)
        image.set_margin_start(0)
        image.set_margin_end(0)
        row.pack_start(image, False, False, 0)

    text = Gtk.Label(label=label)
    text.set_xalign(0.0)
    text.set_margin_start(0)
    row.pack_start(text, False, False, 0)

    item.add(row)
    return item
