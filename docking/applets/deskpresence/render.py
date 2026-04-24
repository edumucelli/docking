"""Pure Cairo rendering for desk-presence applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.deskpresence.state import Presence, format_badge

# Per-status color of strokes and (when applicable) fills.
_STATUS_COLORS: dict[Presence, tuple[float, float, float]] = {
    Presence.AT_DESK: (0.20, 0.55, 0.90),
    Presence.AWAY: (0.95, 0.55, 0.15),
    Presence.UNKNOWN: (0.55, 0.55, 0.60),
}

# Only AT_DESK paints a solid fill; AWAY and UNKNOWN stay as outlines.
_FILLED_STATES = frozenset({Presence.AT_DESK})


def _draw_bust_path(cr: cairo.Context, size: int) -> None:
    """Rounded-shoulder torso path beneath the head, with a clear gap."""
    cx = size / 2
    body_w = size * 0.46
    body_h = size * 0.20
    body_top = size * 0.68
    body_x = cx - body_w / 2
    top_radius = size * 0.11
    bottom_radius = size * 0.03

    cr.new_sub_path()
    cr.move_to(body_x, body_top + top_radius)
    # Top-left rounded corner.
    cr.arc(
        body_x + top_radius,
        body_top + top_radius,
        top_radius,
        math.pi,
        -math.pi / 2,
    )
    cr.line_to(body_x + body_w - top_radius, body_top)
    # Top-right rounded corner.
    cr.arc(
        body_x + body_w - top_radius,
        body_top + top_radius,
        top_radius,
        -math.pi / 2,
        0,
    )
    cr.line_to(body_x + body_w, body_top + body_h - bottom_radius)
    # Bottom-right corner.
    cr.arc(
        body_x + body_w - bottom_radius,
        body_top + body_h - bottom_radius,
        bottom_radius,
        0,
        math.pi / 2,
    )
    cr.line_to(body_x + bottom_radius, body_top + body_h)
    # Bottom-left corner.
    cr.arc(
        body_x + bottom_radius,
        body_top + body_h - bottom_radius,
        bottom_radius,
        math.pi / 2,
        math.pi,
    )
    cr.close_path()


def _draw_presence(
    cr: cairo.Context,
    size: int,
    presence: Presence,
    pulse_phase: float | None = None,
) -> None:
    """Person + two broadcast arcs, tinted by presence."""
    cx = size / 2
    head_cy = size * 0.48
    head_r = size * 0.11
    r, g, b = _STATUS_COLORS[presence]
    stroke_w = max(1.6, size * 0.07)
    filled = presence in _FILLED_STATES

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(stroke_w)

    # Two concentric arcs above the head, sweeping past horizontal so the
    # endpoints curl down alongside the bust.
    arc_extend = 0.35
    cr.set_source_rgb(r, g, b)
    for radius_frac in (0.26, 0.40):
        cr.new_sub_path()
        cr.arc(
            cx,
            head_cy,
            size * radius_frac,
            math.pi - arc_extend,
            math.tau + arc_extend,
        )
        cr.stroke()

    # Pulse ripple: an extra arc that expands outward and fades out. Only
    # draws while AT_DESK, and only when the applet supplies a live phase
    # (catalog thumbnails and tests render without it).
    if pulse_phase is not None and presence is Presence.AT_DESK:
        phase = max(0.0, min(1.0, pulse_phase))
        pulse_r = size * (0.40 + 0.12 * phase)
        pulse_alpha = 0.55 * (1.0 - phase)
        if pulse_alpha > 0.02:
            cr.set_source_rgba(r, g, b, pulse_alpha)
            cr.set_line_width(max(1.2, size * 0.055))
            cr.new_sub_path()
            cr.arc(
                cx,
                head_cy,
                pulse_r,
                math.pi - arc_extend,
                math.tau + arc_extend,
            )
            cr.stroke()
            cr.set_line_width(stroke_w)
            cr.set_source_rgb(r, g, b)

    # Breathing alpha on the fills so AT_DESK gently pulses in sync with the
    # ripple. Strokes stay fully opaque so the outline remains crisp.
    if filled and pulse_phase is not None:
        breath = 0.85 + 0.15 * math.sin(math.tau * pulse_phase)
    else:
        breath = 1.0

    # Head.
    cr.new_sub_path()
    cr.arc(cx, head_cy, head_r, 0, math.tau)
    if filled:
        cr.set_source_rgba(r, g, b, breath)
        cr.fill_preserve()
    cr.set_source_rgb(r, g, b)
    cr.stroke()

    # Torso.
    _draw_bust_path(cr=cr, size=size)
    if filled:
        cr.set_source_rgba(r, g, b, breath)
        cr.fill_preserve()
    cr.set_source_rgb(r, g, b)
    cr.stroke()


def render_icon(
    *,
    size: int,
    presence: Presence,
    at_desk_seconds: float,
    pulse_phase: float | None = None,
) -> GdkPixbuf.Pixbuf | None:
    """Render the presence glyph; outline for AWAY/UNKNOWN, filled for AT_DESK.

    When ``pulse_phase`` is provided (``0.0..1.0``) and presence is AT_DESK,
    an additional ripple arc is drawn expanding outward to animate the icon.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    _draw_presence(cr=cr, size=size, presence=presence, pulse_phase=pulse_phase)
    label = format_badge(at_desk_seconds)
    if label:
        draw_icon_label(cr=cr, text=label, size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
