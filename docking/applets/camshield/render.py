"""Pure Cairo rendering for Cam Shield applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.draw import rounded_rect
from docking.ui.overlays import draw_circle_badge


def render_icon(
    *,
    size: int,
    available: bool,
    active: bool,
    pulse_phase: float | None = None,
) -> GdkPixbuf.Pixbuf | None:
    """Render a webcam shield with a red privacy indicator when active."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    _draw_body(cr=cr, size=size, available=available)
    _draw_lens(cr=cr, size=size, available=available)
    _draw_mount(cr=cr, size=size, available=available)

    if active:
        radius = size * 0.16
        cx = size - radius - size * 0.06
        cy = radius + size * 0.06
        if pulse_phase is not None:
            phase = max(0.0, min(1.0, pulse_phase))
            pulse_radius = radius * (1.15 + 0.65 * phase)
            pulse_alpha = 0.46 * (1.0 - phase)
            if pulse_alpha > 0.02:
                draw_circle_badge(
                    cr=cr,
                    cx=cx,
                    cy=cy,
                    radius=pulse_radius,
                    background_rgba=(0.92, 0.08, 0.10, pulse_alpha),
                )
        draw_circle_badge(
            cr=cr,
            cx=cx,
            cy=cy,
            radius=radius,
            background_rgba=(0.92, 0.08, 0.10, 0.98),
            outline_rgba=(1.0, 1.0, 1.0, 0.92),
            outline_width=max(1.2, size * 0.035),
        )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_body(*, cr: cairo.Context, size: int, available: bool) -> None:
    pad = size * 0.12
    body_y = size * 0.22
    body_h = size * 0.48
    radius = size * 0.14
    rounded_rect(
        cr=cr,
        x=pad,
        y=body_y,
        width=size - 2 * pad,
        height=body_h,
        radius=radius,
    )
    if available:
        cr.set_source_rgba(0.18, 0.26, 0.34, 0.98)
    else:
        cr.set_source_rgba(0.42, 0.44, 0.47, 0.92)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.16)
    cr.set_line_width(max(1.0, size * 0.025))
    cr.stroke()


def _draw_lens(*, cr: cairo.Context, size: int, available: bool) -> None:
    cx = size * 0.50
    cy = size * 0.46
    outer = size * 0.17
    inner = size * 0.09

    cr.arc(cx, cy, outer, 0, math.tau)
    cr.set_source_rgba(0.06, 0.08, 0.10, 0.96)
    cr.fill()

    cr.arc(cx, cy, inner, 0, math.tau)
    if available:
        cr.set_source_rgba(0.28, 0.72, 0.92, 0.95)
    else:
        cr.set_source_rgba(0.72, 0.74, 0.76, 0.75)
    cr.fill()

    cr.arc(cx - inner * 0.32, cy - inner * 0.32, inner * 0.24, 0, math.tau)
    cr.set_source_rgba(1, 1, 1, 0.74)
    cr.fill()


def _draw_mount(*, cr: cairo.Context, size: int, available: bool) -> None:
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(max(2.0, size * 0.07))
    cr.set_source_rgba(0.18, 0.20, 0.23, 0.95 if available else 0.70)
    cr.move_to(size * 0.50, size * 0.70)
    cr.line_to(size * 0.50, size * 0.82)
    cr.stroke()

    cr.set_line_width(max(2.0, size * 0.08))
    cr.move_to(size * 0.34, size * 0.84)
    cr.line_to(size * 0.66, size * 0.84)
    cr.stroke()
