# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Shared themed surfaces for small dock-owned popup windows."""

from __future__ import annotations

from functools import lru_cache

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from docking.platform.environment import compositor_active

STARTUP_POPUP_WINDOW_CLASS = "dock-startup-popup-window"
STARTUP_POPUP_SURFACE_CLASS = "dock-startup-popup-surface"
STARTUP_POPUP_CSS = f"""
.{STARTUP_POPUP_WINDOW_CLASS} {{
    background-color: transparent;
}}

.{STARTUP_POPUP_SURFACE_CLASS} {{
    background-color: @theme_bg_color;
    color: @theme_fg_color;
    border: 1px solid alpha(@theme_fg_color, 0.16);
    border-radius: 12px;
}}
""".encode()


@lru_cache(maxsize=1)
def ensure_startup_popup_css() -> Gtk.CssProvider | None:
    """Install rounded startup-popup CSS once per screen."""
    screen = Gdk.Screen.get_default()
    if screen is None:
        return None

    provider = Gtk.CssProvider()
    provider.load_from_data(STARTUP_POPUP_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    return provider


def configure_transparent_startup_popup_window(window: Gtk.Window) -> None:
    """Make an undecorated popup window transparent behind rounded content."""
    ensure_startup_popup_css()

    if compositor_active() is False:
        return

    window.set_app_paintable(True)

    screen = window.get_screen()
    rgba_visual = None
    if screen is not None:
        rgba_visual = screen.get_rgba_visual()
    if rgba_visual is not None:
        window.set_visual(rgba_visual)

    window.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0))

    window.get_style_context().add_class(STARTUP_POPUP_WINDOW_CLASS)


def wrap_startup_popup_content(content: Gtk.Widget) -> Gtk.Frame:
    """Wrap popup content in the shared rounded dock-startup surface."""
    ensure_startup_popup_css()

    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    frame.get_style_context().add_class(STARTUP_POPUP_SURFACE_CLASS)
    frame.add(content)
    return frame
