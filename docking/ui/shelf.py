"""Shelf background drawing — rounded rectangle, gradient fill, inner highlight."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import cairo

if TYPE_CHECKING:
    from docking.core.theme import Theme

INNER_HIGHLIGHT_OPACITIES = (
    0.5,
    0.12,
    0.08,
    0.19,
)  # top, near-top, near-bottom, bottom


def rounded_rect(
    cr: cairo.Context,
    x: float,
    y: float,
    width: float,
    height: float,
    radius: float,
    round_bottom: bool = True,
) -> None:
    """Draw a rounded rectangle path, optionally with square bottom corners."""
    cr.new_sub_path()
    # Top-right (rounded)
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    if round_bottom:
        # Bottom-right (rounded)
        cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
        # Bottom-left (rounded)
        cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    else:
        # Bottom-right (square)
        cr.line_to(x + width, y + height)
        # Bottom-left (square)
        cr.line_to(x, y + height)
    # Top-left (rounded)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_shelf_background(
    cr: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    theme: Theme,
) -> None:
    """Draw the dock background shelf with Plank-style 3D effect.

    Three layers: gradient fill, dark outer stroke, inner highlight stroke.
    """
    radius = theme.roundness
    line_width = theme.stroke_width

    # Layer 1: Gradient fill + outer stroke
    rounded_rect(
        cr,
        x + line_width / 2,
        y + line_width / 2,
        w - line_width,
        h - line_width / 2,
        radius,
        round_bottom=False,
    )

    pat = cairo.LinearGradient(0, y, 0, y + h)
    pat.add_color_stop_rgba(0, *theme.fill_start)
    pat.add_color_stop_rgba(1, *theme.fill_end)
    cr.set_source(pat)
    cr.fill_preserve()

    cr.set_source_rgba(*theme.stroke)
    cr.set_line_width(line_width)
    cr.stroke()

    # Layer 2: Inner highlight stroke (creates 3D bevel effect)
    # Plank uses white with varying opacity: 50% top → 12% → 8% → 19% bottom
    is_r, is_g, is_b, _ = theme.inner_stroke
    inset = 3 * line_width / 2
    inner_h = h - inset
    top_point = max(radius, line_width) / h if h > 0 else 0.1
    bottom_point = 1.0 - top_point

    highlight = cairo.LinearGradient(0, y + inset, 0, y + h - inset)
    highlight.add_color_stop_rgba(0, is_r, is_g, is_b, INNER_HIGHLIGHT_OPACITIES[0])
    highlight.add_color_stop_rgba(
        top_point, is_r, is_g, is_b, INNER_HIGHLIGHT_OPACITIES[1]
    )
    highlight.add_color_stop_rgba(
        bottom_point, is_r, is_g, is_b, INNER_HIGHLIGHT_OPACITIES[2]
    )
    highlight.add_color_stop_rgba(1, is_r, is_g, is_b, INNER_HIGHLIGHT_OPACITIES[3])

    inner_r = max(radius - line_width, 0)
    rounded_rect(
        cr,
        x + inset,
        y + inset,
        w - 2 * inset,
        inner_h - inset / 2,
        inner_r,
        round_bottom=False,
    )
    cr.set_source(highlight)
    cr.set_line_width(line_width)
    cr.stroke()
