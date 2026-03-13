"""Pure Cairo rendering for the Stretch Coach applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.stretchcoach.state import StretchCoachState


def _draw_head(cr: cairo.Context, size: int) -> None:
    cr.arc(size * 0.5, size * 0.19, size * 0.085, 0, math.tau)
    cr.fill()


def _draw_limbs(cr: cairo.Context, size: int) -> None:
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(max(1.8, size * 0.07))

    # Arms stretched wide/upward.
    cr.move_to(size * 0.5, size * 0.31)
    cr.line_to(size * 0.24, size * 0.18)
    cr.move_to(size * 0.5, size * 0.31)
    cr.line_to(size * 0.76, size * 0.18)

    # Body.
    cr.move_to(size * 0.5, size * 0.29)
    cr.line_to(size * 0.5, size * 0.62)

    # Legs in a grounded stance.
    cr.move_to(size * 0.5, size * 0.62)
    cr.line_to(size * 0.32, size * 0.86)
    cr.move_to(size * 0.5, size * 0.62)
    cr.line_to(size * 0.68, size * 0.86)
    cr.stroke()


def _draw_due_badge(cr: cairo.Context, size: int) -> None:
    badge_radius = size * 0.14
    badge_cx = size * 0.79
    badge_cy = size * 0.24

    cr.arc(badge_cx, badge_cy, badge_radius, 0, math.tau)
    cr.set_source_rgba(0.9, 0.28, 0.22, 1)
    cr.fill_preserve()
    cr.set_line_width(max(1.0, size * 0.03))
    cr.set_source_rgba(1, 1, 1, 0.9)
    cr.stroke()

    cr.set_line_width(max(1.4, size * 0.05))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(badge_cx, badge_cy - badge_radius * 0.45)
    cr.line_to(badge_cx, badge_cy + badge_radius * 0.15)
    cr.stroke()
    cr.arc(badge_cx, badge_cy + badge_radius * 0.48, badge_radius * 0.08, 0, math.tau)
    cr.fill()


def render_icon(size: int, state: StretchCoachState) -> GdkPixbuf.Pixbuf | None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    cr.set_source_rgba(0.96, 0.96, 0.98, 0.16)
    cr.arc(size * 0.5, size * 0.54, size * 0.37, 0, math.tau)
    cr.fill()

    cr.set_source_rgba(0.18, 0.58, 0.78, 1)
    _draw_head(cr=cr, size=size)
    _draw_limbs(cr=cr, size=size)

    if state.due:
        _draw_due_badge(cr=cr, size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
