"""Pure Cairo rendering for Caps Lock applet icon."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.capslock.state import LockKeyState
from docking.applets.draw import rounded_rect

_PANEL = (0.91, 0.89, 0.78)
_PANEL_HIGHLIGHT = (0.98, 0.97, 0.90)
_PANEL_SHADOW = (0.56, 0.56, 0.48)
_INK = (0.04, 0.04, 0.03)
_KEY = (0.96, 0.96, 0.88)
_KEY_UNAVAILABLE = (0.68, 0.68, 0.60)
_LED_ON = (0.08, 0.93, 0.05)
_LED_OFF = (0.16, 0.17, 0.14)
_LED_UNAVAILABLE = (0.42, 0.42, 0.36)


def render_icon(*, size: int, state: LockKeyState) -> GdkPixbuf.Pixbuf | None:
    """Render two keyboard LED indicators for Caps and Num lock."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    _draw_indicator(
        cr=cr,
        size=size,
        cx=size * 0.285,
        label="Num",
        active=state.num_lock,
        available=state.available,
    )
    _draw_indicator(
        cr=cr,
        size=size,
        cx=size * 0.715,
        label="Caps",
        active=state.caps_lock,
        available=state.available,
    )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_indicator(
    *,
    cr: cairo.Context,
    size: int,
    cx: float,
    label: str,
    active: bool,
    available: bool,
) -> None:
    key_w = size * 0.39
    key_h = size * 0.52
    key_x = cx - key_w / 2
    key_y = size * 0.22
    led_w = key_w * 0.48
    led_h = size * 0.075
    led_x = cx - led_w / 2
    led_y = key_y + size * 0.055
    if not available:
        key_color = _KEY_UNAVAILABLE
        led_color = _LED_UNAVAILABLE
    elif active:
        key_color = _KEY
        led_color = _LED_ON
    else:
        key_color = _KEY
        led_color = _LED_OFF

    rounded_rect(
        cr=cr,
        x=key_x - size * 0.014,
        y=key_y - size * 0.014,
        width=key_w + size * 0.028,
        height=key_h + size * 0.028,
        radius=size * 0.025,
    )
    cr.set_source_rgb(*_PANEL_SHADOW)
    cr.fill()

    rounded_rect(
        cr=cr,
        x=key_x,
        y=key_y,
        width=key_w,
        height=key_h,
        radius=size * 0.025,
    )
    cr.set_source_rgb(*key_color)
    cr.fill_preserve()
    cr.set_source_rgb(*_INK)
    cr.set_line_width(max(0.8, size * 0.014))
    cr.stroke()

    cr.set_source_rgba(*_PANEL_HIGHLIGHT, 0.72)
    cr.set_line_width(max(0.7, size * 0.012))
    cr.move_to(key_x + size * 0.035, key_y + size * 0.045)
    cr.line_to(key_x + key_w - size * 0.035, key_y + size * 0.045)
    cr.stroke()

    cr.rectangle(
        led_x - size * 0.01,
        led_y - size * 0.01,
        led_w + size * 0.02,
        led_h + size * 0.02,
    )
    cr.set_source_rgb(*_INK)
    cr.fill()
    cr.rectangle(led_x, led_y, led_w, led_h)
    cr.set_source_rgb(*led_color)
    cr.fill()

    if active and available:
        cr.rectangle(
            led_x + size * 0.018, led_y + size * 0.014, led_w * 0.45, led_h * 0.24
        )
        cr.set_source_rgba(1, 1, 1, 0.30)
        cr.fill()

    cr.save()
    cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_source_rgb(*_INK)
    cr.set_font_size(max(7.0, size * 0.145))
    _draw_centered_text(cr=cr, text=label, cx=cx, baseline=size * 0.64)
    cr.restore()


def _draw_centered_text(
    *,
    cr: cairo.Context,
    text: str,
    cx: float,
    baseline: float,
) -> None:
    ext = cr.text_extents(text)
    cr.move_to(cx - (ext.width / 2 + ext.x_bearing), baseline)
    cr.show_text(text)
