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

"""Rendering helpers for Notifications applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.draw import rounded_rect
from docking.ui.overlays import draw_circle_badge, draw_count_badge


def create_notifications_icon(
    *,
    size: int,
    available: bool = True,
    paused: bool = False,
    badge_count: int = 0,
    activity: bool = False,
) -> GdkPixbuf.Pixbuf | None:
    """Render notifications icon with badge and DND slash."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    pad = size * 0.10
    radius = size * 0.22
    rounded_rect(
        cr=cr, x=pad, y=pad, width=size - 2 * pad, height=size - 2 * pad, radius=radius
    )
    if not available:
        cr.set_source_rgba(0.44, 0.44, 0.46, 0.92)
    elif paused:
        cr.set_source_rgba(0.50, 0.50, 0.52, 0.96)
    else:
        cr.set_source_rgba(0.18, 0.53, 0.94, 0.96)
    cr.fill()

    cx = size * 0.50
    top = size * 0.30
    base = size * 0.64
    body_h = base - top
    half_w = size * 0.24
    neck_half_w = size * 0.06

    # Bell body (classic silhouette: narrow neck, wide rim)
    cr.new_path()
    cr.move_to(cx - neck_half_w, top)
    cr.curve_to(
        cx - half_w * 1.00,
        top + body_h * 0.10,
        cx - half_w * 1.08,
        top + body_h * 0.78,
        cx - half_w * 0.88,
        base,
    )
    cr.line_to(cx + half_w * 0.88, base)
    cr.curve_to(
        cx + half_w * 1.08,
        top + body_h * 0.78,
        cx + half_w * 1.00,
        top + body_h * 0.10,
        cx + neck_half_w,
        top,
    )
    cr.close_path()
    cr.set_source_rgba(1, 1, 1, 0.97)
    cr.fill()

    # Bell crown and stem
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_source_rgba(1, 1, 1, 0.97)
    cr.set_line_width(max(1.4, size * 0.045))
    cr.move_to(cx, top - size * 0.095)
    cr.line_to(cx, top - size * 0.02)
    cr.stroke()
    cr.arc(cx, top - size * 0.105, size * 0.038, 0, math.tau)
    cr.fill()

    # Bell rim
    cr.set_line_width(max(1.8, size * 0.055))
    cr.move_to(cx - half_w * 0.88, base)
    cr.line_to(cx + half_w * 0.88, base)
    cr.stroke()

    # Clapper
    clapper_y = base + size * 0.048
    cr.arc(cx, clapper_y, size * 0.058, 0, math.tau)
    cr.fill()

    if paused:
        cr.set_source_rgba(0.93, 0.24, 0.25, 0.98)
        cr.set_line_width(max(2.0, size * 0.08))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(size * 0.27, size * 0.72)
        cr.line_to(size * 0.72, size * 0.27)
        cr.stroke()

    if available:
        _draw_notification_badge(
            cr=cr,
            size=size,
            badge_count=badge_count,
            activity=activity,
        )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_notification_badge(
    *,
    cr: cairo.Context,
    size: int,
    badge_count: int,
    activity: bool,
) -> None:
    if badge_count <= 0 and not activity:
        return

    radius = size * 0.16
    cx = size - radius - size * 0.06
    cy = size - radius - size * 0.06

    if badge_count > 0:
        draw_count_badge(
            cr=cr,
            x=cx - radius,
            y=cy - radius,
            width=radius * 2,
            height=radius * 2,
            badge_count=badge_count,
        )
        return

    # Unknown count backends: a simple activity chip.
    draw_circle_badge(
        cr=cr,
        cx=cx,
        cy=cy,
        radius=radius,
        background_rgba=(0.98, 0.86, 0.18, 0.98),
    )
    draw_circle_badge(
        cr=cr,
        cx=cx,
        cy=cy,
        radius=radius * 0.34,
        background_rgba=(1.0, 1.0, 1.0, 0.98),
    )
