"""Cairo icon rendering for Power Profiles applet.

Visual language:
- Rounded tile background color communicates profile category immediately.
- Foreground glyph communicates mode:
  - performance: bolt
  - balanced: equal bars
  - power-saver: leaf

Colors are intentionally high-contrast for dock-size icons (small surfaces).
"""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.powerprofiles.state import normalize_profile


def create_power_profiles_icon(
    *,
    size: int,
    profile: str,
    available: bool,
) -> GdkPixbuf.Pixbuf | None:
    """Render profile icon from canonical state."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    profile_norm = normalize_profile(profile)
    bg = _background_color(profile=profile_norm, available=available)
    fg = (1.0, 1.0, 1.0, 1.0) if available else (0.9, 0.9, 0.9, 0.9)

    _draw_rounded_tile(cr=cr, size=size, rgba=bg)

    if profile_norm == "performance":
        _draw_bolt(cr=cr, size=size, rgba=fg)
    elif profile_norm == "power-saver":
        _draw_leaf(cr=cr, size=size, rgba=fg)
    else:
        _draw_balanced(cr=cr, size=size, rgba=fg)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_rounded_tile(
    *,
    cr: cairo.Context,
    size: int,
    rgba: tuple[float, float, float, float],
) -> None:
    """Draw rounded-square tile background."""
    radius = size * 0.26
    pad = size * 0.12
    x = pad
    y = pad
    w = size - 2 * pad
    h = size - 2 * pad

    cr.new_path()
    cr.arc(x + w - radius, y + radius, radius, -1.5708, 0)
    cr.arc(x + w - radius, y + h - radius, radius, 0, 1.5708)
    cr.arc(x + radius, y + h - radius, radius, 1.5708, 3.1416)
    cr.arc(x + radius, y + radius, radius, 3.1416, 4.7124)
    cr.close_path()
    cr.set_source_rgba(*rgba)
    cr.fill()


def _draw_balanced(
    *,
    cr: cairo.Context,
    size: int,
    rgba: tuple[float, float, float, float],
) -> None:
    """Draw two horizontal bars for balanced mode."""
    stroke = max(2.0, size * 0.10)
    x0 = size * 0.30
    x1 = size * 0.70
    y_top = size * 0.42
    y_bottom = size * 0.58
    cr.set_source_rgba(*rgba)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(stroke)
    cr.move_to(x0, y_top)
    cr.line_to(x1, y_top)
    cr.stroke()
    cr.move_to(x0, y_bottom)
    cr.line_to(x1, y_bottom)
    cr.stroke()


def _draw_bolt(
    *,
    cr: cairo.Context,
    size: int,
    rgba: tuple[float, float, float, float],
) -> None:
    """Draw lightning bolt for performance mode."""
    cr.set_source_rgba(*rgba)
    cr.move_to(size * 0.56, size * 0.24)
    cr.line_to(size * 0.36, size * 0.52)
    cr.line_to(size * 0.50, size * 0.52)
    cr.line_to(size * 0.43, size * 0.78)
    cr.line_to(size * 0.66, size * 0.46)
    cr.line_to(size * 0.52, size * 0.46)
    cr.close_path()
    cr.fill()


def _draw_leaf(
    *,
    cr: cairo.Context,
    size: int,
    rgba: tuple[float, float, float, float],
) -> None:
    """Draw simplified leaf + vein for power-saver mode."""
    cx = size * 0.50
    cy = size * 0.50
    w = size * 0.22
    h = size * 0.15
    cr.save()
    cr.translate(cx, cy)
    cr.rotate(-0.5)
    cr.scale(w, h)
    cr.arc(0, 0, 1.0, 0, 6.2832)
    cr.restore()
    cr.set_source_rgba(*rgba)
    cr.fill_preserve()
    cr.set_line_width(max(1.5, size * 0.04))
    cr.stroke()

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(max(1.5, size * 0.045))
    cr.move_to(size * 0.44, size * 0.62)
    cr.line_to(size * 0.58, size * 0.41)
    cr.stroke()


def _background_color(
    *,
    profile: str,
    available: bool,
) -> tuple[float, float, float, float]:
    """Map profile + availability to background tile color."""
    if not available:
        return (0.55, 0.55, 0.55, 0.95)
    if profile == "performance":
        return (0.85, 0.35, 0.22, 0.98)
    if profile == "power-saver":
        return (0.27, 0.64, 0.34, 0.98)
    return (0.92, 0.71, 0.22, 0.98)
