"""Parabolic zoom math — pure functions, no GTK dependency.

Implements the magnification effect from Plank's PositionManager.vala.
Icons near the cursor scale up parabolically; distant icons stay at 1.0x.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.model import DockItem


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
    """Compute icon positions using Plank's per-icon displacement approach.

    Each icon starts at its rest center and gets pushed away from the cursor.
    Distant icons stay put — no cascading shifts.
    """
    n = len(items)
    if n == 0:
        return []

    icon_size = config.icon_size
    zoom_percent = config.zoom_percent if config.zoom_enabled else 1.0
    # Plank: zoom_icon_size = icon_size * zoom_percent (NOT zoom_range)
    # This controls how far the displacement extends — one zoomed icon width
    zoom_icon_size = icon_size * zoom_percent

    # Rest-position centers
    rest_centers: list[float] = []
    x = h_padding + icon_size / 2
    for _ in range(n):
        rest_centers.append(x)
        x += icon_size + item_padding

    result: list[LayoutItem] = []
    for i in range(n):
        center = rest_centers[i]

        if cursor_x < 0:
            # No hover — rest positions
            result.append(LayoutItem(x=center - icon_size / 2, scale=1.0))
            continue

        # Displacement: push icon away from cursor (Plank's formula)
        offset = min(abs(cursor_x - center), zoom_icon_size)
        offset_pct = offset / zoom_icon_size if zoom_icon_size > 0 else 1.0
        if offset_pct > 0.99:
            offset_pct = 1.0

        # Taper the displacement: center icons move more, edge icons barely move
        displacement = offset * (zoom_percent - 1.0) * (1.0 - offset_pct / 3.0)

        # Push away from cursor
        if cursor_x > center:
            center -= displacement
        else:
            center += displacement

        # Zoom scale (same parabolic curve)
        zoom = 1.0 - offset_pct ** 2
        scale = 1.0 + zoom * (zoom_percent - 1.0)

        # Position: center minus half the zoomed icon size
        result.append(LayoutItem(x=center - icon_size * scale / 2, scale=scale))

    return result


def content_bounds(
    layout: list[LayoutItem],
    icon_size: int,
    h_padding: float,
) -> tuple[float, float]:
    """Compute the left and right edges of the content including displacements."""
    if not layout:
        return 0.0, 2 * h_padding
    first = layout[0]
    last = layout[-1]
    left = first.x - h_padding
    right = last.x + icon_size * last.scale + h_padding
    return left, right


def total_width(
    layout: list[LayoutItem],
    icon_size: int,
    item_padding: float,
    h_padding: float,
) -> float:
    """Compute total dock content width from a layout."""
    left, right = content_bounds(layout, icon_size, h_padding)
    return max(right - left, 2 * h_padding)
