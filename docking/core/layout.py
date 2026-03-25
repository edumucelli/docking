"""Shared dock layout primitives used by geometry and rendering.

Why this module exists

The dock needs a pure layout layer that answers questions such as:

- where each item would sit at rest,
- how items displace when the pointer approaches,
- what the content bounds are after that displacement,
- what sentinel value means "there is no active pointer on the dock".

These are not GTK concerns and they are not UI-geometry policy concerns. They
are the low-level layout rules that higher layers consume.

Why this is not just "zoom"

Historically this code lived in `zoom.py`, but the important consumers are now:

- shared dock geometry,
- renderer,
- tests for content bounds and rest layout.

So this module is the correct home for:

- the layout record (`LayoutItem`),
- the no-cursor sentinel,
- the main dock layout algorithm,
- content bounds for that layout.

The zoom-specific scalar helper still lives in `zoom.py`.

Two coordinate ideas

1. Rest layout
   Where each icon would sit with no hover interaction.

2. Hover-adjusted layout
   The same row after nearby items are displaced and scaled.

Each `LayoutItem` records:

- `x`
  main-axis position of the item
- `scale`
  current scale factor
- `width`
  base width of the item slot

This is deliberately one-dimensional. Higher layers decide whether that main
axis is X or Y based on dock position.

How the layout behaves

Every item starts at its rest center. Hover then applies two effects:

- scale
  icons near the pointer become larger

- displacement
  neighbors are pushed away to make room

ASCII sketch:

    rest:
      [A]  [B]  [C]  [D]  [E]

    pointer over C:
               v
      [A] [B] [C^^] [D] [E]
          <-         ->
        pushed     pushed

The important design choice is that displacement is local, not cascading across
the whole dock. Distant items should not visibly jump when the pointer hovers
one icon.

Why the no-cursor sentinel exists

The layout engine needs one explicit value that means:

    "compute rest layout, not a hovered layout"

That value is `NO_CURSOR_SENTINEL`.

This is better than overloading arbitrary negative coordinates because real
layout-local cursor values can legitimately become slightly negative once the
dock's content band is translated into local coordinates.

Content bounds

`content_bounds(...)` answers:

    "Given this layout, what is the left/right extent of the active content?"

That result is used by higher-level geometry to build:

- dock background extents,
- cursor/input regions,
- popup anchors,
- hover and hit regions.

So while `content_bounds` is often consumed by geometry, it belongs here because
it is fundamentally a property of the layout itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem


OFFSET_PCT_SNAP = 0.99
NO_CURSOR_SENTINEL = -1e6


def _has_cursor(cursor_x: float) -> bool:
    """Return True when the cursor value represents a real local coordinate."""
    return cursor_x > (NO_CURSOR_SENTINEL / 2)


@dataclass
class LayoutItem:
    """Computed position and scale for a single dock item."""

    x: float
    scale: float
    width: int = 0


class Bounds(NamedTuple):
    """Left and right edges of content along the main axis."""

    left: float
    right: float


def compute_layout(
    items: list[DockItem],
    config: Config,
    cursor_x: float,
    item_padding: float = 6.0,
    h_padding: float = 12.0,
    zoom_progress: float = 1.0,
) -> list[LayoutItem]:
    """Compute dock item positions and scales for the current pointer state."""
    num_items = len(items)
    if num_items == 0:
        return []

    icon_size = config.icon_size
    base_zoom = config.zoom_percent if config.zoom_enabled else 1.0
    zoom_percent = 1.0 + (base_zoom - 1.0) * zoom_progress
    zoom_icon_size = icon_size * zoom_percent

    widths = [int((item.main_size or icon_size) * item.insert_factor) for item in items]

    rest_centers: list[float] = []
    x = h_padding
    for w in widths:
        pad = item_padding if w > 0 else 0
        x += w / 2
        rest_centers.append(x)
        x += w / 2 + pad

    result: list[LayoutItem] = []
    for i in range(num_items):
        center = rest_centers[i]
        w = widths[i]

        if not _has_cursor(cursor_x):
            result.append(LayoutItem(x=center - w / 2, scale=1.0, width=w))
            continue

        offset = min(abs(cursor_x - center), zoom_icon_size)
        offset_pct = offset / zoom_icon_size if zoom_icon_size > 0 else 1.0
        if offset_pct > OFFSET_PCT_SNAP:
            offset_pct = 1.0

        displacement = offset * (zoom_percent - 1.0) * (1.0 - offset_pct / 3.0)

        if cursor_x > center:
            center -= displacement
        else:
            center += displacement

        zoom = 1.0 - offset_pct**2
        scale = 1.0 + zoom * (zoom_percent - 1.0)
        if not items[i].allow_zoom:
            scale = 1.0

        result.append(LayoutItem(x=center - w * scale / 2, scale=scale, width=w))

    return result


def content_bounds(
    layout: list[LayoutItem],
    icon_size: int,
    h_padding: float,
    item_padding: float = 0.0,
) -> Bounds:
    """Compute the left and right edges of the content including padding."""
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
