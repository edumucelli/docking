"""Cairo rendering for the WhatsApp applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.draw import rounded_rect
from docking.applets.whatsapp.state import BrowserPhase
from docking.ui.overlays import draw_warning_badge


def render_icon(
    *,
    size: int,
    phase: BrowserPhase = BrowserPhase.READY,
) -> GdkPixbuf.Pixbuf | None:
    """Render an original green chat-and-phone icon.

    The artwork deliberately communicates WhatsApp without embedding external
    brand assets. Numeric unread state is left to Docking's shared badge
    renderer so the global badge visibility preference remains authoritative.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    pad = size * 0.07
    if phase is BrowserPhase.UNAVAILABLE:
        top = (0.42, 0.44, 0.46)
        bottom = (0.26, 0.28, 0.30)
    elif phase in {BrowserPhase.ERROR, BrowserPhase.OFFLINE}:
        top = (0.38, 0.53, 0.43)
        bottom = (0.20, 0.34, 0.25)
    elif phase is BrowserPhase.LOGIN_REQUIRED:
        top = (0.96, 0.70, 0.28)
        bottom = (0.71, 0.39, 0.12)
    elif phase in {BrowserPhase.STARTING, BrowserPhase.SYNCING}:
        top = (0.35, 0.82, 0.55)
        bottom = (0.12, 0.59, 0.35)
    else:
        top = (0.28, 0.86, 0.52)
        bottom = (0.08, 0.62, 0.32)

    gradient = cairo.LinearGradient(0, pad, 0, size - pad)
    gradient.add_color_stop_rgba(0, *top, 1.0)
    gradient.add_color_stop_rgba(1, *bottom, 1.0)
    rounded_rect(
        cr=cr,
        x=pad,
        y=pad,
        width=size - pad * 2,
        height=size - pad * 2,
        radius=size * 0.24,
    )
    cr.set_source(gradient)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.30)
    cr.set_line_width(max(1.0, size * 0.032))
    cr.stroke()

    # White speech bubble and its lower-left tail.
    cx = size * 0.50
    cy = size * 0.46
    radius = size * 0.29
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgba(1, 1, 1, 0.97)
    cr.fill()
    cr.move_to(size * 0.29, size * 0.63)
    cr.line_to(size * 0.22, size * 0.78)
    cr.line_to(size * 0.40, size * 0.69)
    cr.close_path()
    cr.fill()

    # A simple handset drawn as one rounded stroke. It stays legible at small
    # dock sizes and avoids depending on a font or external SVG.
    cr.set_source_rgba(*bottom, 1.0)
    cr.set_line_width(max(2.2, size * 0.115))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(size * 0.38, size * 0.32)
    cr.curve_to(
        size * 0.36,
        size * 0.47,
        size * 0.52,
        size * 0.61,
        size * 0.66,
        size * 0.60,
    )
    cr.stroke()
    cr.set_line_width(max(1.5, size * 0.07))
    cr.move_to(size * 0.37, size * 0.30)
    cr.line_to(size * 0.43, size * 0.39)
    cr.move_to(size * 0.61, size * 0.54)
    cr.line_to(size * 0.69, size * 0.60)
    cr.stroke()

    if phase in {
        BrowserPhase.ERROR,
        BrowserPhase.OFFLINE,
        BrowserPhase.UNAVAILABLE,
    }:
        draw_warning_badge(cr=cr, size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
