"""Procedural icon rendering for the Gmail applet."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.draw import rounded_rect
from docking.applets.gmail.state import GmailStatus, unread_badge_text


def create_icon(
    *,
    size: int,
    status: GmailStatus,
    unread_count: int,
) -> GdkPixbuf.Pixbuf | None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    base_rgba = _base_rgba(status=status)
    border_alpha = 0.96 if status in {GmailStatus.CONNECTED, GmailStatus.STALE} else 0.7

    pad = size * 0.12
    width = size - pad * 2
    height = size - pad * 2
    radius = size * 0.12

    rounded_rect(cr=cr, x=pad, y=pad, width=width, height=height, radius=radius)
    cr.set_source_rgba(*base_rgba)
    cr.fill_preserve()
    cr.set_line_width(max(1.4, size * 0.04))
    cr.set_source_rgba(0.85, 0.14, 0.16, border_alpha)
    cr.stroke()

    left = pad
    top = pad
    right = left + width
    bottom = top + height
    mid_x = (left + right) / 2
    flap_y = top + height * 0.46

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(max(2.0, size * 0.07))
    cr.set_source_rgba(0.83, 0.13, 0.17, border_alpha)

    cr.move_to(left + width * 0.06, top + height * 0.14)
    cr.line_to(mid_x, flap_y)
    cr.line_to(right - width * 0.06, top + height * 0.14)
    cr.stroke()

    cr.move_to(left + width * 0.06, bottom - height * 0.12)
    cr.line_to(mid_x, top + height * 0.54)
    cr.line_to(right - width * 0.06, bottom - height * 0.12)
    cr.stroke()

    _draw_badge(cr=cr, size=size, unread_count=unread_count, status=status)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _base_rgba(*, status: GmailStatus) -> tuple[float, float, float, float]:
    if status == GmailStatus.CONNECTED:
        return (0.98, 0.98, 0.98, 1.0)
    if status == GmailStatus.STALE:
        return (0.96, 0.94, 0.90, 1.0)
    if status == GmailStatus.RECONNECT_REQUIRED:
        return (0.99, 0.93, 0.86, 1.0)
    if status == GmailStatus.ERROR:
        return (0.96, 0.90, 0.90, 1.0)
    if status == GmailStatus.CONNECTING:
        return (0.94, 0.95, 0.98, 1.0)
    return (0.86, 0.86, 0.88, 0.92)


def _draw_badge(
    *,
    cr: cairo.Context,
    size: int,
    unread_count: int,
    status: GmailStatus,
) -> None:
    if unread_count <= 0:
        if status not in {GmailStatus.CONNECTING, GmailStatus.RECONNECT_REQUIRED}:
            return
        radius = size * 0.11
        cx = size - radius - size * 0.08
        cy = size - radius - size * 0.08
        cr.arc(cx, cy, radius, 0, math.tau)
        cr.set_source_rgba(0.98, 0.74, 0.18, 0.96)
        cr.fill()
        return

    radius = size * 0.17
    cx = size - radius - size * 0.06
    cy = size - radius - size * 0.06
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgba(0.82, 0.14, 0.17, 1.0)
    cr.fill()

    text = unread_badge_text(unread_count)
    if unread_count <= 9:
        font_size = size * 0.22
    elif unread_count <= 99:
        font_size = size * 0.165
    else:
        font_size = size * 0.125
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(max(7, font_size))
    ext = cr.text_extents(text)
    cr.set_source_rgba(1, 1, 1, 1)
    cr.move_to(
        cx - (ext.width / 2 + ext.x_bearing),
        cy - (ext.height / 2 + ext.y_bearing),
    )
    cr.show_text(text)
