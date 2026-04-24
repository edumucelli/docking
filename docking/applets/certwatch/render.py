"""Pure Cairo rendering for certwatch applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.certwatch.state import CertStatus

# RGB triples per status. The palette is intentionally vivid so the
# badge reads clearly at small icon sizes.
_STATUS_COLORS: dict[CertStatus, tuple[float, float, float]] = {
    CertStatus.OK: (0.30, 0.75, 0.35),
    CertStatus.WARN: (0.95, 0.75, 0.15),
    CertStatus.CRITICAL: (0.90, 0.35, 0.25),
    CertStatus.EXPIRED: (0.60, 0.10, 0.10),
    CertStatus.ERROR: (0.65, 0.65, 0.70),
    CertStatus.UNKNOWN: (0.50, 0.55, 0.65),
}


def _draw_shield_path(cr: cairo.Context, size: int) -> None:
    """Draw a classic shield outline centered in a size x size canvas."""
    pad = size * 0.10
    top_y = pad
    bot_y = size - pad * 0.6
    left_x = pad
    right_x = size - pad
    mid_x = size / 2
    shoulder_y = size * 0.28

    cr.new_path()
    cr.move_to(mid_x, top_y)
    cr.line_to(right_x, shoulder_y)
    # Right side curves in towards the point.
    cr.curve_to(
        right_x,
        size * 0.65,
        right_x - (right_x - mid_x) * 0.55,
        bot_y,
        mid_x,
        bot_y,
    )
    cr.curve_to(
        left_x + (mid_x - left_x) * 0.55,
        bot_y,
        left_x,
        size * 0.65,
        left_x,
        shoulder_y,
    )
    cr.close_path()


def _draw_padlock(cr: cairo.Context, size: int) -> None:
    """Draw a small padlock centered on the shield."""
    cx = size / 2
    body_w = size * 0.30
    body_h = size * 0.22
    body_x = cx - body_w / 2
    body_y = size * 0.42

    # Shackle: half-circle above the body.
    shackle_radius = body_w * 0.32
    shackle_cx = cx
    shackle_cy = body_y

    cr.save()
    cr.set_line_width(max(1.5, size * 0.05))
    cr.set_source_rgba(1, 1, 1, 0.95)
    cr.arc(shackle_cx, shackle_cy, shackle_radius, math.pi, math.tau)
    cr.stroke()

    # Body rectangle.
    radius = size * 0.03
    cr.new_path()
    cr.move_to(body_x + radius, body_y)
    cr.line_to(body_x + body_w - radius, body_y)
    cr.arc(
        body_x + body_w - radius,
        body_y + radius,
        radius,
        -math.pi / 2,
        0,
    )
    cr.line_to(body_x + body_w, body_y + body_h - radius)
    cr.arc(
        body_x + body_w - radius,
        body_y + body_h - radius,
        radius,
        0,
        math.pi / 2,
    )
    cr.line_to(body_x + radius, body_y + body_h)
    cr.arc(
        body_x + radius,
        body_y + body_h - radius,
        radius,
        math.pi / 2,
        math.pi,
    )
    cr.line_to(body_x, body_y + radius)
    cr.arc(
        body_x + radius,
        body_y + radius,
        radius,
        math.pi,
        -math.pi / 2,
    )
    cr.close_path()
    cr.set_source_rgba(1, 1, 1, 0.95)
    cr.fill()
    cr.restore()


def _draw_shield(cr: cairo.Context, size: int, status: CertStatus) -> None:
    red, green, blue = _STATUS_COLORS[status]

    # Filled shield body.
    _draw_shield_path(cr=cr, size=size)
    cr.set_source_rgb(red, green, blue)
    cr.fill()

    # Subtle outline for crisp edges at small sizes.
    _draw_shield_path(cr=cr, size=size)
    cr.set_source_rgba(0, 0, 0, 0.4)
    cr.set_line_width(max(1.0, size * 0.025))
    cr.stroke()

    _draw_padlock(cr=cr, size=size)


def render_icon(
    *,
    size: int,
    status: CertStatus,
    label: str,
) -> GdkPixbuf.Pixbuf | None:
    """Render a shield icon colored by status, with an optional day count."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    _draw_shield(cr=cr, size=size, status=status)
    if label:
        draw_icon_label(cr=cr, text=label, size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
