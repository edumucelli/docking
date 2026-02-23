"""Theme loading and color accessors.

The theme system uses a SCALING UNIT for all layout values stored in the
JSON theme files.  The unit is "tenths of one percent of icon_size":

    pixel_value = json_value * (icon_size / 10.0)

For example, with icon_size=48 the scale factor is 4.8:
    json h_padding=0  -> 0 * 4.8 =  0.0 px
    json item_padding=2.5 -> 2.5 * 4.8 = 12.0 px
    json top_padding=-7  -> -7 * 4.8 = -33.6 px (icons overflow above shelf)

This scaling unit means a single theme JSON produces correct proportions
at ANY icon size -- 32px, 48px, 64px, 128px, etc.  All downstream code
receives pixel values from the Theme dataclass and never touches the raw
JSON scaling values.

Animation parameters (bounce heights, durations, opacity) are NOT scaled
-- they are stored as-is from the JSON since they are already in their
final units (fractions, milliseconds, opacity 0-1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Bundled themes directory (relative to package)
_BUILTIN_THEMES_DIR = Path(__file__).resolve().parent.parent / "assets" / "themes"

# Color types as Cairo-compatible floats (0.0-1.0)
RGB = tuple[float, float, float]
RGBA = tuple[float, float, float, float]


def _rgba(values: list[int]) -> RGBA:
    """Convert [R, G, B, A] (0-255) to Cairo-compatible (0.0-1.0) tuple."""
    return values[0] / 255, values[1] / 255, values[2] / 255, values[3] / 255


@dataclass(frozen=True)
class Theme:
    """Visual theme for the dock.

    All layout fields store PIXEL values, computed at load time from the
    JSON scaling units.  Downstream rendering code uses these directly.
    """

    # --- Colors (Cairo 0.0-1.0) ---
    fill_start: RGBA = (41 / 255, 41 / 255, 41 / 255, 1.0)
    fill_end: RGBA = (80 / 255, 80 / 255, 80 / 255, 1.0)
    stroke: RGBA = (41 / 255, 41 / 255, 41 / 255, 1.0)
    stroke_width: float = 1.0
    inner_stroke: RGBA = (1.0, 1.0, 1.0, 1.0)
    roundness: float = 4.0
    indicator_color: RGBA = (1.0, 1.0, 1.0, 200 / 255)
    active_indicator_color: RGBA = (100 / 255, 180 / 255, 1.0, 1.0)

    # --- Layout (stored as px after scaling) ---
    indicator_radius: float = 2.5
    h_padding: float = 12.0
    top_padding: float = 4.0
    bottom_padding: float = 8.0
    item_padding: float = 6.0
    shelf_height: float = 21.0

    # --- Animation (direct values, NOT scaled) ---
    urgent_bounce_height: float = 1.66  # fraction of icon_size
    launch_bounce_height: float = 0.625  # fraction of icon_size
    urgent_bounce_time_ms: int = 600  # ms
    launch_bounce_time_ms: int = 600  # ms
    click_time_ms: int = 300  # ms
    hover_lighten: float = 0.2  # 0.0-1.0 additive brightness
    active_time_ms: int = 150  # ms for hover fade in/out
    max_indicator_dots: int = 3  # max running indicator dots
    glow_opacity: float = 0.6  # active glow gradient max opacity

    @classmethod
    def load(cls, name: str = "default", icon_size: int = 48) -> "Theme":
        """Load theme by name, applying the scaling unit system.

        The JSON theme file stores layout values in a SCALING UNIT:
        "tenths of one percent of icon_size."  At load time we multiply
        each layout value by `icon_size / 10.0` to get pixel values.

        Example with icon_size=48 (scale factor = 4.8):

            JSON value   x  scale   =  pixel value
            ---------      -----      -----------
            h_padding=0     4.8        0.0 px
            item_padding=2.5 4.8       12.0 px
            top_padding=-7   4.8      -33.6 px  (negative = icons above shelf)
            bottom_padding=1 4.8        4.8 px
            indicator_size=5 4.8       24.0 px -> radius = 12.0 px ... no,
                                                  indicator_size is special:
                                                  stored as raw px, not scaled.

        Note: `indicator_size` in the JSON maps to `indicator_radius` and is
        stored as half the JSON value (radius = size / 2), NOT scaled.

        HOW SHELF HEIGHT IS DERIVED:

        The shelf is the background bar.  Icons sit ON the shelf, often
        overflowing above it.  shelf_height is derived, not stored in JSON:

            shelf_height = max(0, icon_size + top_offset + bottom_offset)

        where:
            top_offset    = 2 * stroke_width + top_padding_px
            bottom_offset = bottom_padding_px

        (bottom_roundness=0 in Plank's Yaru-light, so no extra offset.)

        For default theme at 48px icons:
            scaled          = 48 / 10 = 4.8
            top_padding_px  = -7 * 4.8 = -33.6
            bottom_padding_px = 1 * 4.8 = 4.8
            top_offset      = 2 * 1.0 + (-33.6) = -31.6
            bottom_offset   = 4.8
            shelf_height    = max(0, 48 + (-31.6) + 4.8) = 21.2

        ASCII diagram of the geometry:

            ===== icon top =====
            |                  |
            |      icon        |   icon_size (48px)
            |      (48px)      |
            |                  |
            === shelf top ===  |   <- top_offset from icon top
            |  shelf bg     |  |      (2*stroke + top_padding)
            |               |  |
            ================== |   <- shelf bottom = screen bottom
              bottom_offset        (bottom_padding)

        The Plank h_padding fallback:
            When h_padding <= 0 in the JSON (producing 0px or negative),
            effective h_padding becomes 2 * stroke_width.  This mirrors
            Plank's `items_offset = 2*LineWidth + (HorizPadding>0 ? HorizPadding : 0)`.

        Animation parameters (bounce heights, durations, etc.) are loaded
        directly from JSON without scaling -- they are already in their
        final units (fractions of icon_size, milliseconds, opacity).

        Args:
            name: Theme name (matches filename without .json extension).
            icon_size: Icon size in pixels (default 48).

        Returns:
            A Theme instance with all layout values in pixels.
        """
        path = _BUILTIN_THEMES_DIR / f"{name}.json"
        if not path.exists():
            return cls()

        with open(path) as f:
            data: dict[str, Any] = json.load(f)

        # --- Scale factor ---
        # All layout values in JSON use "tenths of percent of icon_size".
        # Multiply by this to convert to pixels.
        scaled = icon_size / 10.0

        # --- Colors (not scaled, just converted 0-255 -> 0.0-1.0) ---
        fill_start = _rgba(data.get("fill_start", [40, 40, 40, 220]))
        fill_end = _rgba(data.get("fill_end", [30, 30, 30, 220]))
        stroke = _rgba(data.get("stroke", [41, 41, 41, 255]))
        stroke_width = float(data.get("stroke_width", 1.0))
        inner_stroke = _rgba(data.get("inner_stroke", [255, 255, 255, 255]))
        roundness = float(data.get("roundness", 4.0))
        indicator_color = _rgba(data.get("indicator_color", [255, 255, 255, 200]))
        active_indicator_color = _rgba(
            data.get("active_indicator_color", [100, 180, 255, 255])
        )

        # --- Layout values: JSON scaling unit -> pixels ---
        # indicator_size is stored as raw pixels (diameter), halved to radius.
        indicator_radius = float(data.get("indicator_size", 5)) / 2.0

        # h_padding: Plank fallback -- when JSON value <= 0, use 2*stroke_width
        raw_h_padding = float(data.get("h_padding", 0))
        h_padding_px = raw_h_padding * scaled
        if h_padding_px <= 0:
            h_padding_px = 2.0 * stroke_width

        top_padding_px = float(data.get("top_padding", -7)) * scaled
        bottom_padding_px = float(data.get("bottom_padding", 1)) * scaled
        item_padding_px = float(data.get("item_padding", 2.5)) * scaled

        # --- Derive shelf_height ---
        # shelf_height = max(0, icon_size + top_offset + bottom_offset)
        # top_offset = 2 * stroke_width + top_padding_px
        # bottom_offset = bottom_padding_px  (bottom_roundness=0)
        top_offset = 2.0 * stroke_width + top_padding_px
        bottom_offset = bottom_padding_px
        shelf_height = max(0.0, icon_size + top_offset + bottom_offset)

        # --- Animation params (direct values, NOT scaled) ---
        urgent_bounce_height = float(data.get("urgent_bounce_height", 1.66))
        launch_bounce_height = float(data.get("launch_bounce_height", 0.625))
        urgent_bounce_time_ms = int(data.get("urgent_bounce_time_ms", 600))
        launch_bounce_time_ms = int(data.get("launch_bounce_time_ms", 600))
        click_time_ms = int(data.get("click_time_ms", 300))
        hover_lighten = float(data.get("hover_lighten", 0.2))
        active_time_ms = int(data.get("active_time_ms", 150))
        max_indicator_dots = int(data.get("max_indicator_dots", 3))
        glow_opacity = float(data.get("glow_opacity", 0.6))

        return cls(
            fill_start=fill_start,
            fill_end=fill_end,
            stroke=stroke,
            stroke_width=stroke_width,
            inner_stroke=inner_stroke,
            roundness=roundness,
            indicator_color=indicator_color,
            active_indicator_color=active_indicator_color,
            indicator_radius=indicator_radius,
            h_padding=h_padding_px,
            top_padding=top_padding_px,
            bottom_padding=bottom_padding_px,
            item_padding=item_padding_px,
            shelf_height=shelf_height,
            urgent_bounce_height=urgent_bounce_height,
            launch_bounce_height=launch_bounce_height,
            urgent_bounce_time_ms=urgent_bounce_time_ms,
            launch_bounce_time_ms=launch_bounce_time_ms,
            click_time_ms=click_time_ms,
            hover_lighten=hover_lighten,
            active_time_ms=active_time_ms,
            max_indicator_dots=max_indicator_dots,
            glow_opacity=glow_opacity,
        )
