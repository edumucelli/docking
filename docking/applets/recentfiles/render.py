"""Pure Cairo renderer for Recent Files applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402


def _draw_document(*, cr: cairo.Context, size: int, has_files: bool) -> None:
    """Draw a document icon with folded corner, text lines, and clock overlay."""
    pad = size * 0.12
    doc_w = size * 0.58
    doc_h = size * 0.72
    dx = pad
    dy = pad * 0.6
    fold = size * 0.14

    # Document body with folded corner
    cr.new_path()
    cr.move_to(dx, dy)
    cr.line_to(dx + doc_w - fold, dy)
    cr.line_to(dx + doc_w, dy + fold)
    cr.line_to(dx + doc_w, dy + doc_h)
    cr.line_to(dx, dy + doc_h)
    cr.close_path()

    if has_files:
        cr.set_source_rgb(0.95, 0.95, 0.97)
    else:
        cr.set_source_rgb(0.80, 0.80, 0.82)
    cr.fill_preserve()
    cr.set_source_rgb(0.55, 0.55, 0.58)
    cr.set_line_width(max(1.0, size * 0.03))
    cr.stroke()

    # Fold triangle
    cr.new_path()
    cr.move_to(dx + doc_w - fold, dy)
    cr.line_to(dx + doc_w - fold, dy + fold)
    cr.line_to(dx + doc_w, dy + fold)
    cr.close_path()
    cr.set_source_rgb(0.82, 0.82, 0.85)
    cr.fill_preserve()
    cr.set_source_rgb(0.55, 0.55, 0.58)
    cr.set_line_width(max(0.8, size * 0.02))
    cr.stroke()

    # Text lines
    cr.set_source_rgb(0.60, 0.60, 0.63)
    cr.set_line_width(max(1.0, size * 0.03))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    line_x = dx + size * 0.08
    line_end = dx + doc_w - size * 0.10
    for i in range(3):
        y = dy + size * 0.22 + i * size * 0.14
        end = line_end if i < 2 else line_x + (line_end - line_x) * 0.6
        cr.move_to(line_x, y)
        cr.line_to(end, y)
        cr.stroke()

    # Clock overlay (bottom-right)
    cx = size * 0.72
    cy = size * 0.72
    r = size * 0.18

    # Clock background
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.set_source_rgb(0.20, 0.55, 0.85)
    cr.fill()

    # Clock face
    cr.arc(cx, cy, r * 0.78, 0, 2 * math.pi)
    cr.set_source_rgb(0.95, 0.95, 0.97)
    cr.fill()

    # Clock hands
    cr.set_source_rgb(0.20, 0.20, 0.25)
    cr.set_line_width(max(1.0, size * 0.035))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    # Hour hand (pointing ~10 o'clock)
    cr.move_to(cx, cy)
    cr.line_to(cx - r * 0.35, cy - r * 0.35)
    cr.stroke()
    # Minute hand (pointing 12)
    cr.move_to(cx, cy)
    cr.line_to(cx, cy - r * 0.55)
    cr.stroke()


def render_icon(*, size: int, has_files: bool) -> GdkPixbuf.Pixbuf | None:
    """Render the Recent Files applet icon."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    _draw_document(cr=cr, size=size, has_files=has_files)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
