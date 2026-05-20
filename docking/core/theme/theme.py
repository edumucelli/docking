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

"""Theme loading for dock colors, layout proportions, and animation constants.

What a theme controls in this dock

A theme is not just "colors". In this project it defines three categories of
visual behavior:

1. colors
   shelf fill, strokes, indicators, glows

2. layout proportions
   padding, spacing, shelf height, roundness, distance from edge

3. animation constants
   bounce heights, durations, glow timing, hover lighten amounts

So the theme layer is the visual design system for the dock.

Why themes use scaled layout units

Theme JSON files store most layout values in a unit tied to icon size:

    pixel_value = json_value * (icon_size / 10.0)

That means one theme can scale proportionally across several icon sizes without
needing separate 32px / 48px / 64px variants.

Example with `icon_size = 48`:

    scale factor = 48 / 10 = 4.8

    json item_padding = 2.5
      -> 2.5 * 4.8 = 12px

    json top_padding = -7
      -> -7 * 4.8 = -33.6px

Why negative padding exists

Negative top padding is not a bug. It is how the theme makes icons overflow
above the shelf instead of sitting entirely inside a rectangular bar.

ASCII view:

        icon overflow
           /\
          /  \
         |    |
    -----+----+-----  shelf top
    |   shelf body  |
    -----------------  screen edge / shelf bottom

This is the classic dock look: icons appear to stand on a shelf rather than sit
inside a toolbar.

Derived shelf height

The theme file does not directly store final shelf height in pixels. Shelf
height is derived from:

- icon size
- top padding
- bottom padding
- stroke width

That matters because shelf height is the result of how the icon and shelf are
supposed to overlap, not an arbitrary separate constant.

Conceptually:

    shelf_height =
        icon_size
      + top_offset
      + bottom_offset

where top offset may be negative.

Color conversion boundary

Theme JSON stores colors as 0-255 RGBA integer arrays. The renderer uses Cairo,
which wants 0.0-1.0 float tuples. This module is the conversion boundary:

    JSON [R, G, B, A]
      |
      +--> Theme RGBA tuple (0.0-1.0)

That means downstream rendering code never needs to know the raw JSON format.

Scaled vs unscaled values

Not everything in a theme should scale with icon size.

Scaled:
- shelf/layout proportions
- spacing/padding

Not scaled:
- milliseconds
- opacity fractions
- relative bounce fractions
- style enums/booleans

This distinction is important. A theme should keep the same temporal feel at
different icon sizes rather than making all timing longer just because icons got
bigger.

Theme as runtime input

By the time the rest of the dock sees a `Theme` instance, it is already a
fully-resolved runtime object:

- pixel values are computed,
- colors are normalized,
- enums are parsed,
- derived shelf geometry is available.

Downstream code should not need to re-interpret raw JSON theme semantics.
"""

from __future__ import annotations

import enum
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from docking.core.theme.migration import migrate_loaded_theme_data
from docking.log import get_logger

# Bundled themes directory (relative to package)
_BUILTIN_THEMES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "assets" / "themes"
)
_USER_THEME_TEMPLATE_NAME = "template"
log = get_logger("theme")

# Color types as Cairo-compatible floats (0.0-1.0)
RGB = tuple[float, float, float]
RGBA = tuple[float, float, float, float]

_USER_THEME_TEMPLATE = {
    "shelf": {
        "fill_start_color": [222, 222, 222, 240],
        "fill_end_color": [247, 247, 247, 240],
        "stroke_color": [145, 145, 145, 255],
        "stroke_width_px": 1.0,
        "inner_stroke_color": [248, 248, 248, 255],
        "corner_radius_px": 5,
        "round_bottom": False,
    },
    "layout": {
        "horizontal_padding": 0,
        "top_padding": -7,
        "bottom_padding": 1,
        "item_padding": 2.5,
        "distance_from_edge_px": 0,
    },
    "indicators": {
        "inactive_color": [80, 80, 80, 200],
        "active_color": [50, 50, 50, 255],
        "size_px": 5,
        "style": "dots",
        "max_dots": 4,
    },
    "items": {
        "hover": {
            "lighten_amount": 0.2,
            "fade_ms": 150,
        },
        "bounce": {
            "urgent_height_ratio": 1.66,
            "launch_height_ratio": 0.625,
            "urgent_time_ms": 600,
            "launch_time_ms": 600,
            "click_time_ms": 300,
        },
        "glow": {
            "opacity_ratio": 0.6,
            "urgent_time_ms": 10000,
            "urgent_pulse_ms": 2000,
            "urgent_size_ratio": 0.6,
        },
    },
}


def user_themes_dir() -> Path:
    """Return the user-writable theme directory."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "docking" / "themes"


def ensure_user_theme_template() -> None:
    """Create the user theme directory and editable template if missing."""
    directory = user_themes_dir()
    template = directory / f"{_USER_THEME_TEMPLATE_NAME}.json"
    if template.exists():
        _migrate_existing_user_theme_template(path=template, directory=directory)
        return
    directory.mkdir(parents=True, exist_ok=True)
    template.write_text(
        json.dumps(_USER_THEME_TEMPLATE, indent=2) + "\n",
        encoding="utf-8",
    )


def _migrate_existing_user_theme_template(*, path: Path, directory: Path) -> None:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        log.warning("Failed to inspect user theme template %s: %s", path, exc)
        return
    if not isinstance(data, dict):
        log.warning(
            "User theme template %s is not a JSON object; leaving it unchanged",
            path,
        )
        return
    migrate_loaded_theme_data(data=data, path=path, user_theme_dir=directory)


def _theme_paths(name: str) -> list[Path]:
    return [
        user_themes_dir() / f"{name}.json",
        _BUILTIN_THEMES_DIR / f"{name}.json",
    ]


def list_theme_names() -> list[str]:
    """Return built-in and user theme names, excluding the copy/edit template."""
    ensure_user_theme_template()
    names = {p.stem for p in _BUILTIN_THEMES_DIR.glob("*.json")}
    names.update(
        p.stem
        for p in user_themes_dir().glob("*.json")
        if p.stem != _USER_THEME_TEMPLATE_NAME
    )
    return sorted(names)


class IndicatorStyle(str, enum.Enum):
    DOTS = "dots"
    DASHES = "dashes"


def _rgba(values: list[int]) -> RGBA:
    """Convert [R, G, B, A] (0-255) to Cairo-compatible (0.0-1.0) tuple."""
    return values[0] / 255, values[1] / 255, values[2] / 255, values[3] / 255


def _multiply_alpha(color: RGBA, multiplier: float) -> RGBA:
    return color[0], color[1], color[2], color[3] * multiplier


def _theme_value(data: Mapping[str, Any], path: str, default: Any) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


@dataclass(frozen=True)
class Theme:
    """Visual theme for the dock.

    All layout fields store pixel values, computed at load time from the
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
    horizontal_padding: float = 12.0
    top_padding: float = 4.0
    bottom_padding: float = 8.0
    item_padding: float = 6.0
    shelf_height: float = 21.0

    # --- Animation (direct values, not scaled) ---
    urgent_bounce_height: float = 1.66  # fraction of icon_size
    launch_bounce_height: float = 0.625  # fraction of icon_size
    urgent_bounce_time_ms: int = 600  # ms
    launch_bounce_time_ms: int = 600  # ms
    click_time_ms: int = 300  # ms
    hover_lighten: float = 0.2  # 0.0-1.0 additive brightness
    active_time_ms: int = 150  # ms for hover fade in/out
    max_indicator_dots: int = 4  # max running indicator dots
    glow_opacity: float = 0.6  # active glow gradient max opacity
    urgent_glow_time_ms: int = 10000  # glow visible for 10s after urgency
    urgent_glow_pulse_ms: int = 2000  # one pulse cycle every 2s
    urgent_glow_size: float = 0.6  # glow radius as fraction of icon_size
    indicator_style: IndicatorStyle = IndicatorStyle.DOTS
    round_bottom: bool = False  # round bottom corners (vs square flush with edge)
    distance_from_edge: int = 0  # gap between dock and screen edge in pixels

    def with_opacity(self, multiplier: float) -> Theme:
        """Return a copy whose RGBA colors keep their hue but scale alpha."""
        return replace(
            self,
            fill_start=_multiply_alpha(self.fill_start, multiplier),
            fill_end=_multiply_alpha(self.fill_end, multiplier),
            stroke=_multiply_alpha(self.stroke, multiplier),
            inner_stroke=_multiply_alpha(self.inner_stroke, multiplier),
            indicator_color=_multiply_alpha(self.indicator_color, multiplier),
            active_indicator_color=_multiply_alpha(
                self.active_indicator_color, multiplier
            ),
        )

    @classmethod
    def load(cls, name: str = "default", icon_size: int = 48) -> Theme:
        """Load theme by name, applying the scaling unit system.

        The JSON theme file stores layout values in a scaling unit:
        "tenths of one percent of icon_size."  At load time we multiply
        each layout value by `icon_size / 10.0` to get pixel values.

        Example with icon_size=48 (scale factor = 4.8):

            JSON value   x  scale   =  pixel value
            ---------      -----      -----------
            horizontal_padding=0
                            4.8        0.0 px
            item_padding=2.5 4.8       12.0 px
            top_padding=-7   4.8      -33.6 px  (negative = icons above shelf)
            bottom_padding=1 4.8        4.8 px
            indicator_size=5 4.8       24.0 px -> radius = 12.0 px ... no,
                                                  indicator_size is special:
                                                  stored as raw px, not scaled.

        Note: `indicator_size` in the JSON maps to `indicator_radius` and is
        stored as half the JSON value (radius = size / 2), not scaled.

        How shelf height is derived:

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

        The Plank horizontal padding fallback:
            When horizontal_padding <= 0 in the JSON (producing 0px or negative),
            effective horizontal_padding becomes 2 * stroke_width.  This mirrors
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
        ensure_user_theme_template()
        path = next(
            (candidate for candidate in _theme_paths(name) if candidate.exists()),
            None,
        )
        if path is None:
            return cls()

        with path.open(encoding="utf-8") as f:
            data: dict[str, Any] = json.load(fp=f)
        data = migrate_loaded_theme_data(
            data=data,
            path=path,
            user_theme_dir=user_themes_dir(),
        )

        # --- Scale factor ---
        # All layout values in JSON use "tenths of percent of icon_size".
        # Multiply by this to convert to pixels.
        scaled = icon_size / 10.0

        # --- Colors (not scaled, just converted 0-255 -> 0.0-1.0) ---
        fill_start = _rgba(
            values=_theme_value(data, "shelf.fill_start_color", [40, 40, 40, 220])
        )
        fill_end = _rgba(
            values=_theme_value(data, "shelf.fill_end_color", [30, 30, 30, 220])
        )
        stroke = _rgba(
            values=_theme_value(data, "shelf.stroke_color", [41, 41, 41, 255])
        )
        stroke_width = float(_theme_value(data, "shelf.stroke_width_px", 1.0))
        inner_stroke = _rgba(
            values=_theme_value(data, "shelf.inner_stroke_color", [255, 255, 255, 255])
        )
        roundness = float(_theme_value(data, "shelf.corner_radius_px", 4.0))
        indicator_color = _rgba(
            values=_theme_value(data, "indicators.inactive_color", [255, 255, 255, 200])
        )
        active_indicator_color = _rgba(
            values=_theme_value(data, "indicators.active_color", [100, 180, 255, 255])
        )

        # --- Layout values: JSON scaling unit -> pixels ---
        # indicator_size is stored as raw pixels (diameter), halved to radius.
        indicator_radius = float(_theme_value(data, "indicators.size_px", 5)) / 2.0

        # horizontal_padding: Plank fallback -- when JSON value <= 0,
        # use 2*stroke_width.
        raw_horizontal_padding = float(
            _theme_value(data, "layout.horizontal_padding", 0)
        )
        horizontal_padding_px = raw_horizontal_padding * scaled
        if horizontal_padding_px <= 0:
            horizontal_padding_px = 2.0 * stroke_width

        top_padding_px = float(_theme_value(data, "layout.top_padding", -7)) * scaled
        bottom_padding_px = (
            float(_theme_value(data, "layout.bottom_padding", 1)) * scaled
        )
        item_padding_px = float(_theme_value(data, "layout.item_padding", 2.5)) * scaled

        # --- Derive shelf_height ---
        #
        # The dock has two visual layers: the shelf (background bar) and
        # the icons that sit on top of it. The shelf is intentionally
        # shorter than the icons, creating a "shelf" effect where icons
        # overflow above the background:
        #
        #     ┌──────┐          ┌──────┐
        #     │ icon │          │ icon │       <- icons overflow above shelf
        #     │      │          │      │
        #   ──┴──────┴──────────┴──────┴──   <- shelf top edge
        #   │        shelf background       │
        #   ─────────────────────────────────  <- screen bottom
        #
        # The height is derived from the icon size and theme padding:
        #   top_offset    = 2 * stroke_width + top_padding_px
        #   bottom_offset = bottom_padding_px
        #   shelf_height  = max(0, icon_size + top_offset + bottom_offset)
        #
        # With the default theme at 48px icons:
        #   top_offset = 2 + (-33.6) = -31.6  (negative -> icons overflow)
        #   bottom_offset = 4.8
        #   shelf_height = max(0, 48 - 31.6 + 4.8) ≈ 21px
        #
        # This means icons extend ~27px above the shelf, which gives
        # the characteristic dock appearance.
        top_offset = 2.0 * stroke_width + top_padding_px
        bottom_offset = bottom_padding_px
        shelf_height = max(0.0, icon_size + top_offset + bottom_offset)

        # --- Animation params (direct values, not scaled) ---
        urgent_bounce_height = float(
            _theme_value(data, "items.bounce.urgent_height_ratio", 1.66)
        )
        launch_bounce_height = float(
            _theme_value(data, "items.bounce.launch_height_ratio", 0.625)
        )
        urgent_bounce_time_ms = int(
            _theme_value(data, "items.bounce.urgent_time_ms", 600)
        )
        launch_bounce_time_ms = int(
            _theme_value(data, "items.bounce.launch_time_ms", 600)
        )
        click_time_ms = int(_theme_value(data, "items.bounce.click_time_ms", 300))
        hover_lighten = float(_theme_value(data, "items.hover.lighten_amount", 0.2))
        active_time_ms = int(_theme_value(data, "items.hover.fade_ms", 150))
        max_indicator_dots = int(_theme_value(data, "indicators.max_dots", 4))
        glow_opacity = float(_theme_value(data, "items.glow.opacity_ratio", 0.6))
        urgent_glow_time_ms = int(
            _theme_value(data, "items.glow.urgent_time_ms", 10000)
        )
        urgent_glow_pulse_ms = int(
            _theme_value(data, "items.glow.urgent_pulse_ms", 2000)
        )
        urgent_glow_size = float(
            _theme_value(data, "items.glow.urgent_size_ratio", 0.6)
        )
        raw_indicator_style = _theme_value(data, "indicators.style", "dots")
        try:
            indicator_style = IndicatorStyle(raw_indicator_style)
        except ValueError as exc:
            log.warning(
                "Invalid indicator style %r; using %r (%s)",
                raw_indicator_style,
                IndicatorStyle.DOTS.value,
                exc,
            )
            indicator_style = IndicatorStyle.DOTS
        round_bottom = bool(_theme_value(data, "shelf.round_bottom", False))
        distance_from_edge = int(_theme_value(data, "layout.distance_from_edge_px", 0))

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
            horizontal_padding=horizontal_padding_px,
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
            urgent_glow_time_ms=urgent_glow_time_ms,
            urgent_glow_pulse_ms=urgent_glow_pulse_ms,
            urgent_glow_size=urgent_glow_size,
            indicator_style=indicator_style,
            round_bottom=round_bottom,
            distance_from_edge=distance_from_edge,
        )
