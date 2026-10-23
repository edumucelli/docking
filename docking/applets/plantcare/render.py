"""Pure Cairo rendering for the Plant Care applet."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.plantcare.state import CareStatus, PlantCareSnapshot


def _status_color(status: CareStatus) -> tuple[float, float, float]:
    if status is CareStatus.OVERDUE:
        return (0.90, 0.20, 0.20)
    if status is CareStatus.DUE:
        return (0.95, 0.62, 0.12)
    if status is CareStatus.HEALTHY:
        return (0.20, 0.72, 0.34)
    return (0.48, 0.52, 0.56)


def _leaf(
    cr: cairo.Context,
    *,
    start_x: float,
    start_y: float,
    tip_x: float,
    tip_y: float,
    size: int,
) -> None:
    """Draw one outlined leaf between a stem point and tip."""
    dx = tip_x - start_x
    dy = tip_y - start_y
    width = size * 0.10
    length = max(size * 0.18, (dx * dx + dy * dy) ** 0.5)
    nx = -dy / length * width
    ny = dx / length * width

    cr.new_path()
    cr.move_to(start_x, start_y)
    cr.curve_to(
        start_x + dx * 0.35 + nx,
        start_y + dy * 0.35 + ny,
        tip_x - dx * 0.22 + nx * 0.55,
        tip_y - dy * 0.22 + ny * 0.55,
        tip_x,
        tip_y,
    )
    cr.curve_to(
        tip_x - dx * 0.22 - nx * 0.55,
        tip_y - dy * 0.22 - ny * 0.55,
        start_x + dx * 0.35 - nx,
        start_y + dy * 0.35 - ny,
        start_x,
        start_y,
    )
    cr.close_path()


def _draw_pot(cr: cairo.Context, *, size: int, empty: bool) -> None:
    """Draw a compact flower pot at the bottom of the icon."""
    left = size * 0.25
    right = size * 0.75
    top = size * 0.66
    bottom = size * 0.91
    inset = size * 0.07

    cr.new_path()
    cr.move_to(left, top)
    cr.line_to(right, top)
    cr.line_to(right - inset, bottom)
    cr.line_to(left + inset, bottom)
    cr.close_path()
    if empty:
        cr.set_source_rgba(0.35, 0.37, 0.40, 0.92)
    else:
        cr.set_source_rgba(0.58, 0.31, 0.17, 0.96)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.86)
    cr.set_line_width(max(1.2, size * 0.032))
    cr.stroke()

    cr.rectangle(left - size * 0.025, top - size * 0.055, size * 0.55, size * 0.09)
    cr.set_source_rgba(0.48, 0.25, 0.14, 0.98)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.86)
    cr.stroke()


def _draw_sprout(
    cr: cairo.Context,
    *,
    size: int,
    status: CareStatus,
) -> None:
    color = _status_color(status)
    cx = size * 0.50
    stem_bottom = size * 0.66
    stem_top = size * 0.25
    droop = status is CareStatus.OVERDUE
    due = status is CareStatus.DUE

    cr.set_source_rgb(*color)
    cr.set_line_width(max(2.0, size * 0.065))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(cx, stem_bottom)
    cr.curve_to(
        cx - size * 0.015,
        size * 0.53,
        cx + size * 0.02,
        size * 0.38,
        cx,
        stem_top,
    )
    cr.stroke()

    left_tip_y = size * (0.55 if droop else 0.33 if due else 0.29)
    right_tip_y = size * (0.49 if droop else 0.27 if due else 0.22)
    leaves = (
        (cx, size * 0.49, size * 0.20, left_tip_y),
        (cx, size * 0.40, size * 0.81, right_tip_y),
    )
    for start_x, start_y, tip_x, tip_y in leaves:
        _leaf(
            cr,
            start_x=start_x,
            start_y=start_y,
            tip_x=tip_x,
            tip_y=tip_y,
            size=size,
        )
        cr.set_source_rgb(*color)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.84)
        cr.set_line_width(max(1.0, size * 0.026))
        cr.stroke()

    if status is CareStatus.OVERDUE:
        _draw_alert_mark(cr=cr, size=size)


def _draw_alert_mark(cr: cairo.Context, *, size: int) -> None:
    cx = size * 0.79
    cy = size * 0.20
    radius = size * 0.115
    cr.arc(cx, cy, radius, 0, 2 * 3.141592653589793)
    cr.set_source_rgba(0.18, 0.07, 0.07, 0.95)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.94)
    cr.set_line_width(max(1.0, size * 0.025))
    cr.stroke()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(max(1.4, size * 0.035))
    cr.move_to(cx, cy - radius * 0.48)
    cr.line_to(cx, cy + radius * 0.12)
    cr.stroke()
    cr.arc(cx, cy + radius * 0.55, size * 0.012, 0, 2 * 3.141592653589793)
    cr.fill()


def render_icon(
    *,
    size: int,
    snapshot: PlantCareSnapshot,
) -> GdkPixbuf.Pixbuf | None:
    """Render the current Plant Care state as a dock icon."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    empty = snapshot.status is CareStatus.EMPTY
    if not empty:
        _draw_sprout(cr=cr, size=size, status=snapshot.status)
    _draw_pot(cr=cr, size=size, empty=empty)
    if snapshot.due_count:
        draw_icon_label(cr=cr, text=str(snapshot.due_count), size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
