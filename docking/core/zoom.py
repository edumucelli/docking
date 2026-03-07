"""Parabolic dock zoom geometry used by rendering and interaction.

This module defines the shared math for icon scale and icon position under
cursor hover. It is used by both rendering code and hit-testing logic. Keeping
that geometry in one place prevents class of bugs where icons are drawn at one
position but click/drag logic thinks they are somewhere else.

Two coordinate systems are involved

1. rest layout: icon centers/slots when no hover zoom is applied,
2. zoomed layout: per-icon displacement and scale relative to the cursor.

Every icon begins at its rest center. Hover then applies two effects:

- scale: icons near cursor become larger,
- displacement: neighbors are pushed away to make room.

Both effects depend on normalized cursor distance.

Scale function used

Scale follows a parabolic falloff:

    zoom = 1 - offset_pct^2
    scale = 1 + zoom * (zoom_percent - 1)

``offset_pct`` is distance from cursor normalized to the configured zoom range:

- ``offset_pct = 0`` (cursor centered on icon) -> max scale,
- ``offset_pct = 1`` (at zoom boundary) -> scale is exactly 1.0.

Why parabolic instead of linear

Linear falloff makes too many neighbors look equally emphasized. The quadratic
curve concentrates emphasis around the hovered icon and gives a smoother visual
transition to non-hovered neighbors.

Small numeric example

If ``zoom_percent = 1.5``:

- center icon (offset_pct=0.0) -> scale = 1.5
- mid neighbor (offset_pct=0.5) -> zoom=0.75 -> scale=1.375
- boundary icon (offset_pct=1.0) -> zoom=0.0 -> scale=1.0

Why this module is pure functions only

No GTK objects are touched here. Inputs are scalar config/cursor values and
items; output is a list of plain ``LayoutItem`` records. That keeps behavior
deterministic, easy to unit test, and cheap to call from high-frequency UI
paths.

Autohide interaction

``zoom_progress`` modulates effective zoom during show/hide animation. As the
dock hides, zoom and displacement decay together back to rest geometry, which
prevents snapping artifacts when the dock transitions.

Floating-point boundary handling

Near the zoom edge, floating-point values can land at ``0.999999`` instead of
``1.0``. ``OFFSET_PCT_SNAP`` snaps that region to 1.0 so edge icons do not
receive tiny residual displacement/scale and visually twitch at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem


# Floating-point snap threshold for the zoom offset percentage.
#
# offset_pct is the normalized distance from cursor to icon center,
# ranging from 0.0 (directly under cursor) to 1.0 (at max zoom range).
# Due to floating-point arithmetic, this value may land at 0.9999...
# instead of exactly 1.0 at the boundary.
#
# Without snapping, icons at the very edge of the zoom range would get
# a tiny residual displacement and scale change -- visible as a subtle
# "twitch" when hovering near the zoom boundary. Snapping at 0.99
# ensures these edge icons are treated as fully outside the zoom range.
OFFSET_PCT_SNAP = 0.99
NO_CURSOR_SENTINEL = -1e6


def _has_cursor(cursor_x: float) -> bool:
    """Return True when the cursor value represents a real local coordinate."""
    return cursor_x > (NO_CURSOR_SENTINEL / 2)


@dataclass
class LayoutItem:
    """Computed position and scale for a single dock icon."""

    x: float
    scale: float
    width: int = 0  # Per-item base width (0 = icon_size)


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
        cursor_x: Current cursor X position, or `NO_CURSOR_SENTINEL` if the
            pointer is not present on the dock at all.
        icon_center_x: Center X of the icon at rest (no zoom).
        icon_size: Base icon size in pixels.
        zoom_percent: Maximum zoom multiplier (e.g. 2.0 for 2x).
        zoom_range: Number of icon widths over which zoom tapers off.

    Returns:
        Scale factor (1.0 = no zoom, zoom_percent = full zoom).
    """
    if not _has_cursor(cursor_x):
        return 1.0

    max_distance = icon_size * zoom_range
    offset = min(abs(cursor_x - icon_center_x), max_distance)
    offset_pct = offset / max_distance if max_distance > 0 else 1.0
    zoom = 1.0 - offset_pct**2
    return 1.0 + zoom * (zoom_percent - 1.0)


def compute_layout(
    items: list[DockItem],
    config: Config,
    cursor_x: float,
    item_padding: float = 6.0,
    h_padding: float = 12.0,
    zoom_progress: float = 1.0,
) -> list[LayoutItem]:
    """Compute icon positions using Plank's per-icon displacement approach.

    Each icon starts at its rest center and gets pushed away from the cursor.
    Distant icons stay put -- no cascading shifts.

    zoom_progress (0-1) scales the effective zoom. During autohide, this
    decays from 1.0 to 0.0, collapsing both icon scale AND displacement
    so icons compress toward their rest centers (Plank's zoom_in_percent).
    """
    num_items = len(items)
    if num_items == 0:
        return []

    icon_size = config.icon_size
    base_zoom = config.zoom_percent if config.zoom_enabled else 1.0
    # Effective zoom decays with zoom_progress (matches Plank's zoom_in_percent)
    zoom_percent = 1.0 + (base_zoom - 1.0) * zoom_progress
    # Zoom displacement radius.
    #
    # This value controls how far the displacement effect extends from
    # the cursor. Icons within this distance get pushed away from the
    # cursor to make room for the zoomed icon. Icons beyond this
    # distance stay at their rest positions.
    #
    # Set to one zoomed icon width (icon_size * zoom_percent). For
    # example, with 48px icons and 1.5x zoom, the radius is 72px.
    # This means only the immediate neighbors of the hovered icon
    # are significantly displaced -- distant icons barely move.
    #
    # A larger radius (e.g., icon_size * zoom_range) would spread
    # the displacement across more icons, causing visible shifts
    # even for far-away items. The tighter radius keeps the effect
    # local and focused.
    zoom_icon_size = icon_size * zoom_percent

    # Per-item widths (0 = use icon_size)
    widths = [item.main_size or icon_size for item in items]

    # Rest-position centers
    rest_centers: list[float] = []
    x = h_padding
    for w in widths:
        x += w / 2
        rest_centers.append(x)
        x += w / 2 + item_padding

    result: list[LayoutItem] = []
    for i in range(num_items):
        center = rest_centers[i]
        w = widths[i]

        if not _has_cursor(cursor_x):
            # No hover -- rest positions
            result.append(LayoutItem(x=center - w / 2, scale=1.0, width=w))
            continue

        # Per-icon displacement: push icons away from cursor.
        #
        # Each icon is displaced from its rest (no-zoom) center position.
        # The displacement direction is away from the cursor -- icons to
        # the left of the cursor shift left, icons to the right shift right.
        # This creates space for the zoomed icon under the cursor:
        #
        #   Cursor at C:        v
        #   Rest positions:  [A]  [B]  [C]  [D]  [E]
        #   After zoom:      [A] [B]  [C^^] [D] [E]
        #                        <-         ->
        #                    pushed    pushed
        #                    left      right
        #
        # The displacement amount depends on distance from cursor:
        #   offset     = distance from cursor, capped to zoom_icon_size
        #   offset_pct = offset / zoom_icon_size  (0.0 = on cursor, 1.0 = at max range)
        #
        # The displacement formula has three terms:
        #   displacement = offset * (zoom_percent - 1.0) * (1.0 - offset_pct / 3.0)
        #
        #   Term 1: offset (base displacement proportional to distance)
        #   Term 2: (zoom_percent - 1.0) (more zoom = more spread)
        #   Term 3: (1.0 - offset_pct / 3.0) (taper factor, pulls edges inward)
        #
        # The taper factor reduces displacement by up to 33% at the edges
        # (offset_pct=1.0 -> factor=0.667). This prevents icons at the zoom
        # boundary from jumping discontinuously.
        offset = min(abs(cursor_x - center), zoom_icon_size)
        offset_pct = offset / zoom_icon_size if zoom_icon_size > 0 else 1.0
        if offset_pct > OFFSET_PCT_SNAP:
            offset_pct = 1.0

        displacement = offset * (zoom_percent - 1.0) * (1.0 - offset_pct / 3.0)

        if cursor_x > center:
            center -= displacement
        else:
            center += displacement

        # Zoom scale: parabolic curve.
        #
        # The icon scale follows a parabolic (quadratic) curve based on
        # distance from cursor:
        #   zoom = 1.0 - offset_pct²
        #   scale = 1.0 + zoom * (zoom_percent - 1.0)
        #
        # At offset_pct=0.0 (directly under cursor): scale = zoom_percent (max zoom)
        # At offset_pct=0.5 (halfway to edge): scale = 1.0 + 0.75 * (zoom_percent - 1.0)
        # At offset_pct=1.0 (at max range): scale = 1.0 (no zoom)
        #
        # The quadratic curve (²) creates a smooth, natural-looking
        # falloff -- most zoom is concentrated on the hovered icon with
        # a gentle taper to its neighbors.
        zoom = 1.0 - offset_pct**2
        scale = 1.0 + zoom * (zoom_percent - 1.0)
        if not items[i].allow_zoom:
            scale = 1.0

        # Position: center minus half the zoomed item size
        result.append(LayoutItem(x=center - w * scale / 2, scale=scale, width=w))

    return result


class Bounds(NamedTuple):
    """Left and right edges of content along the main axis."""

    left: float
    right: float


def content_bounds(
    layout: list[LayoutItem],
    icon_size: int,
    h_padding: float,
    item_padding: float = 0.0,
) -> Bounds:
    """Compute the left and right edges of the content including displacements.

    Plank treats each icon slot as (icon_size + item_padding) wide, so the
    shelf extends item_padding/2 beyond the first/last icon edges. We add
    this to h_padding to match.
    """
    half_item_pad = item_padding / 2
    pad = h_padding + half_item_pad
    if not layout:
        return Bounds(left=0.0, right=2 * pad)
    first = layout[0]
    last = layout[-1]
    left = first.x - pad
    last_w = last.width or icon_size
    right = last.x + last_w * last.scale + pad
    return Bounds(left=left, right=right)


def total_width(
    layout: list[LayoutItem],
    icon_size: int,
    h_padding: float,
    item_padding: float = 0.0,
) -> float:
    """Compute total dock content width from a layout."""
    left, right = content_bounds(
        layout=layout,
        icon_size=icon_size,
        h_padding=h_padding,
        item_padding=item_padding,
    )
    return max(right - left, 2 * (h_padding + item_padding / 2))
