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

"""Pure Cairo rendering for Mic Shield."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.core.math import clamp
from docking.ui.overlays import draw_circle_badge

_BODY_ACTIVE = (0.22, 0.62, 0.86)
_BODY_IDLE = (0.52, 0.56, 0.62)
_BODY_MUTED = (0.34, 0.36, 0.40)
_STROKE = (0.08, 0.10, 0.13)
_SLASH = (0.92, 0.20, 0.22)
_DOT = (0.92, 0.08, 0.10)


def render_icon(
    *,
    size: int,
    available: bool,
    muted: bool,
    active: bool,
    pulse_phase: float | None = None,
) -> GdkPixbuf.Pixbuf | None:
    """Render a microphone glyph with mute and privacy states."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    body = _BODY_MUTED if muted else _BODY_ACTIVE if active else _BODY_IDLE
    alpha = 1.0 if available else 0.52
    _draw_microphone(cr=cr, size=size, color=body, alpha=alpha)
    if muted:
        _draw_slash(cr=cr, size=size)
    if active:
        _draw_privacy_dot(cr=cr, size=size, pulse_phase=pulse_phase)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_microphone(
    *,
    cr: cairo.Context,
    size: int,
    color: tuple[float, float, float],
    alpha: float,
) -> None:
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    cx = size * 0.48
    top = size * 0.17
    body_w = size * 0.25
    body_h = size * 0.43
    radius = body_w / 2

    cr.new_sub_path()
    cr.arc(cx, top + radius, radius, math.pi, 0)
    cr.line_to(cx + radius, top + body_h - radius)
    cr.arc(cx, top + body_h - radius, radius, 0, math.pi)
    cr.close_path()
    cr.set_source_rgba(color[0], color[1], color[2], alpha)
    cr.fill_preserve()
    cr.set_source_rgba(*_STROKE, 0.82 * alpha)
    cr.set_line_width(max(1.2, size * 0.035))
    cr.stroke()

    cr.set_source_rgba(*_STROKE, 0.74 * alpha)
    cr.set_line_width(max(2.0, size * 0.065))
    cr.arc(cx, size * 0.46, size * 0.29, 0.12 * math.pi, 0.88 * math.pi)
    cr.stroke()

    cr.move_to(cx, size * 0.70)
    cr.line_to(cx, size * 0.83)
    cr.stroke()
    cr.move_to(cx - size * 0.15, size * 0.84)
    cr.line_to(cx + size * 0.15, size * 0.84)
    cr.stroke()

    cr.set_source_rgba(1, 1, 1, 0.24 * alpha)
    cr.set_line_width(max(1.0, size * 0.024))
    cr.move_to(cx - body_w * 0.20, top + body_h * 0.18)
    cr.line_to(cx - body_w * 0.20, top + body_h * 0.54)
    cr.stroke()
    cr.restore()


def _draw_slash(*, cr: cairo.Context, size: int) -> None:
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(max(2.4, size * 0.085))
    cr.set_source_rgba(*_SLASH, 0.96)
    cr.move_to(size * 0.24, size * 0.76)
    cr.line_to(size * 0.76, size * 0.24)
    cr.stroke()
    cr.set_line_width(max(1.0, size * 0.025))
    cr.set_source_rgba(1, 1, 1, 0.82)
    cr.move_to(size * 0.24, size * 0.76)
    cr.line_to(size * 0.76, size * 0.24)
    cr.stroke()
    cr.restore()


def _draw_privacy_dot(
    *,
    cr: cairo.Context,
    size: int,
    pulse_phase: float | None,
) -> None:
    radius = size * 0.15
    cx = size - radius - size * 0.06
    cy = radius + size * 0.06
    if pulse_phase is not None:
        phase = clamp(pulse_phase, 0.0, 1.0)
        pulse_radius = radius * (1.15 + 0.65 * phase)
        pulse_alpha = 0.46 * (1.0 - phase)
        if pulse_alpha > 0.02:
            draw_circle_badge(
                cr=cr,
                cx=cx,
                cy=cy,
                radius=pulse_radius,
                background_rgba=(*_DOT, pulse_alpha),
            )
    draw_circle_badge(
        cr=cr,
        cx=cx,
        cy=cy,
        radius=radius,
        background_rgba=(*_DOT, 0.98),
        outline_rgba=(1.0, 1.0, 1.0, 0.92),
        outline_width=max(1.2, size * 0.035),
    )
