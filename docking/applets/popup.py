"""Shared themed popup helpers for applet-owned popup windows."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

_POPUP_CLASS = "applet-popup-surface"
_POPUP_CSS = f"""
.{_POPUP_CLASS} {{
    background-color: @theme_bg_color;
    color: @theme_fg_color;
    border: 1px solid alpha(@theme_fg_color, 0.16);
    border-radius: 12px;
}}
""".encode()

_popup_css_provider: Gtk.CssProvider | None = None


def ensure_popup_css() -> None:
    """Install the shared popup CSS once per screen."""
    global _popup_css_provider
    if _popup_css_provider is not None:
        return

    screen = Gdk.Screen.get_default()
    if screen is None:
        return

    provider = Gtk.CssProvider()
    provider.load_from_data(_POPUP_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _popup_css_provider = provider


def wrap_popup(content: Gtk.Widget) -> Gtk.Frame:
    """Wrap popup content in a GTK-themed surface instead of custom painting."""
    ensure_popup_css()

    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    frame.get_style_context().add_class(_POPUP_CLASS)
    frame.add(content)
    return frame
