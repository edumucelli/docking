"""Parabolic zoom math — pure functions, no GTK dependency.

Implements the magnification effect from Plank's PositionManager.vala.
Icons near the cursor scale up parabolically; distant icons stay at 1.0x.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.config import Config
    from docking.dock_model import DockItem


@dataclass
class LayoutItem:
    """Computed position and scale for a single dock icon."""
    x: float
    scale: float


def compute_icon_zoom(
    cursor_x: float,
    icon_center_x: float,
    icon_size: int,
    zoom_percent: float,
    zoom_range: int,
) -> float:
    """Compute zoom scale for a single icon based on cursor distance.

    Uses the parabolic formula from Plank: zoom = 1 - (offset_pct)^2,
    scaled to the configured zoom_percent.

    Args:
        cursor_x: Current cursor X position, or -1 if cursor is off dock.
        icon_center_x: Center X of the icon at rest (no zoom).
        icon_size: Base icon size in pixels.
        zoom_percent: Maximum zoom multiplier (e.g. 2.0 for 2x).
        zoom_range: Number of icon widths over which zoom tapers off.

    Returns:
        Scale factor (1.0 = no zoom, zoom_percent = full zoom).
    """
    if cursor_x < 0:
        return 1.0

    max_distance = icon_size * zoom_range
    offset = min(abs(cursor_x - icon_center_x), max_distance)
    offset_pct = offset / max_distance if max_distance > 0 else 1.0
    zoom = 1.0 - offset_pct ** 2
    return 1.0 + zoom * (zoom_percent - 1.0)


def compute_layout(
    items: list[DockItem],
    config: Config,
    cursor_x: float,
    item_padding: float = 6.0,
    h_padding: float = 12.0,
) -> list[LayoutItem]:
    """Compute the X position and scale of each icon with zoom applied.

    Two-pass algorithm:
    1. Compute scales based on rest positions (approximate).
    2. Layout with actual zoomed sizes to get final X positions.

    Returns:
        List of LayoutItem with x position and scale for each icon.
    """
    n = len(items)
    if n == 0:
        return []

    icon_size = config.icon_size
    zoom_percent = config.zoom_percent if config.zoom_enabled else 1.0
    zoom_range = config.zoom_range

    # Pass 1: compute rest-position centers and initial scales
    rest_x = h_padding
    centers: list[float] = []
    for _ in range(n):
        center = rest_x + icon_size / 2
        centers.append(center)
        rest_x += icon_size + item_padding

    scales = [
        compute_icon_zoom(cursor_x, c, icon_size, zoom_percent, zoom_range)
        for c in centers
    ]

    # Pass 2: layout with zoomed sizes
    x = h_padding
    result: list[LayoutItem] = []
    for i in range(n):
        result.append(LayoutItem(x=x, scale=scales[i]))
        x += icon_size * scales[i] + item_padding

    return result


def total_width(
    layout: list[LayoutItem],
    icon_size: int,
    item_padding: float,
    h_padding: float,
) -> float:
    """Compute total dock content width from a layout."""
    if not layout:
        return 2 * h_padding
    last = layout[-1]
    return last.x + icon_size * last.scale + h_padding
