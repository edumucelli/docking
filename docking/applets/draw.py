"""Shared Cairo drawing helpers for applet renderers."""

from __future__ import annotations

import math

import cairo


def rounded_rect(
    *,
    cr: cairo.Context,
    x: float,
    y: float,
    width: float,
    height: float,
    radius: float,
) -> None:
    """Add a closed rounded-rectangle sub-path to *cr*."""
    radius = max(0.0, min(radius, min(width, height) / 2))
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
