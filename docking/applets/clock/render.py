"""Pure Cairo rendering for Clock applet."""

from __future__ import annotations

import time
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Pango, PangoCairo

from docking.applets.clock.state import hour_rotation_12h, minute_rotation

_CLOCK_THEMES_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "clock"

# SVG layers composited bottom-to-top for the analog face
_FACE_LAYERS = [
    "clock-drop-shadow",
    "clock-face-shadow",
    "clock-face",
    "clock-marks",
]
_TOP_LAYERS = [
    "clock-glass",
    "clock-frame",
]


def _paint_svg(cr: cairo.Context, path: Path, size: int) -> None:
    """Load an SVG at the given size and paint it onto the Cairo context."""
    pbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), size, size)
    Gdk.cairo_set_source_pixbuf(cr, pbuf, 0, 0)
    cr.paint()


def render_analog(
    cr: cairo.Context,
    size: int,
    now: time.struct_time,
) -> None:
    """Draw analog clock: SVG face layers, Cairo hands, SVG glass+frame."""
    center = size / 2
    radius = center
    theme_dir = _CLOCK_THEMES_DIR / "Default"

    # Bottom SVG layers: shadow, face, marks
    for name in _FACE_LAYERS:
        _paint_svg(cr=cr, path=theme_dir / f"{name}.svg", size=size)

    # Hands (drawn between face and glass/frame layers)
    lw = max(1.0, size / 48.0)
    minute = now.tm_min
    hour = now.tm_hour

    cr.translate(center, center)
    cr.set_line_width(lw)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    # Minute hand (dark gray, longer)
    cr.save()
    cr.set_source_rgba(0.15, 0.15, 0.15, 1)
    cr.rotate(minute_rotation(minute=minute))
    cr.move_to(0, radius - radius * 0.35)
    cr.line_to(0, -radius * 0.15)
    cr.stroke()
    cr.restore()

    # Hour hand (black, shorter)
    cr.save()
    cr.set_source_rgba(0, 0, 0, 1)
    cr.rotate(hour_rotation_12h(hour=hour, minute=minute))
    cr.move_to(0, radius - radius * 0.5)
    cr.line_to(0, -radius * 0.15)
    cr.stroke()
    cr.restore()

    cr.translate(-center, -center)

    # Top SVG layers: glass highlight, frame bezel
    for name in _TOP_LAYERS:
        _paint_svg(cr=cr, path=theme_dir / f"{name}.svg", size=size)


def _draw_outlined_text(
    cr: cairo.Context,
    layout: Pango.Layout,
    x: float,
    y: float,
    stroke_width: float,
    fill_rgba: tuple[float, ...] = (1, 1, 1, 1),
) -> None:
    """Draw Pango text with a black outline and colored fill."""
    cr.save()
    cr.move_to(x, y)
    PangoCairo.layout_path(cr, layout)
    cr.set_source_rgba(0, 0, 0, 1)
    cr.set_line_width(stroke_width)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.stroke_preserve()
    cr.set_source_rgba(*fill_rgba)
    cr.fill()
    cr.restore()


def _draw_am_pm(
    cr: cairo.Context,
    size: int,
    y: float,
    font: Pango.FontDescription,
    is_pm: bool,
) -> None:
    """Draw AM/PM indicators: active one bright, inactive dim."""
    center = size / 2
    quarter = size / 4
    for label, active, x_center in [
        ("AM", not is_pm, quarter),
        ("PM", is_pm, center + quarter),
    ]:
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(font)
        layout.set_text(label, -1)
        _, logical = layout.get_pixel_extents()
        tx = x_center - logical.width / 2 - logical.x
        alpha = 1.0 if active else 0.35
        _draw_outlined_text(
            cr=cr,
            layout=layout,
            x=tx,
            y=y - logical.y,
            stroke_width=2.5,
            fill_rgba=(1, 1, 1, alpha),
        )


def render_digital(
    cr: cairo.Context,
    size: int,
    now: time.struct_time,
    is_24h: bool,
    show_date: bool,
) -> None:
    """Draw outlined digital time (and optionally date + AM/PM)."""
    center = size / 2

    # Build list of (text, font_desc, stroke_width, rgba) rows to draw
    rows: list[tuple[str, Pango.FontDescription, float, tuple[float, ...]]] = []

    # Time text
    if is_24h:
        time_str = time.strftime("%H:%M", now)
    else:
        time_str = time.strftime("%l:%M", now).strip()
    time_font_size = max(1, int(size / 4))
    time_font = Pango.FontDescription(f"Sans Bold {time_font_size}px")
    rows.append((time_str, time_font, 3.0, (1, 1, 1, 1)))

    # Date text (digital mode only)
    if show_date:
        date_str = time.strftime("%b %-d", now)
        date_font_size = max(1, int(size / 5))
        date_font = Pango.FontDescription(f"Sans Bold {date_font_size}px")
        rows.append((date_str, date_font, 2.5, (1, 1, 1, 1)))

    # Measure all rows to compute vertical layout
    layouts = []
    total_h = 0
    spacing = max(1, int(size * 0.04))
    for text, font, _sw, _rgba in rows:
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(font)
        layout.set_text(text, -1)
        _, logical = layout.get_pixel_extents()
        layouts.append((layout, logical))
        total_h += logical.height

    # AM/PM indicator (12h mode only, below time)
    am_pm_height = 0
    am_pm_font_size = max(1, int(size / 5))
    am_pm_font = Pango.FontDescription(f"Sans Bold {am_pm_font_size}px")
    if not is_24h:
        tmp_layout = PangoCairo.create_layout(cr)
        tmp_layout.set_font_description(am_pm_font)
        tmp_layout.set_text("AM", -1)
        _ink, am_logical = tmp_layout.get_pixel_extents()
        am_pm_height = am_logical.height
        total_h += am_pm_height

    num_gaps = len(rows) - 1 + (1 if not is_24h else 0)
    total_h += num_gaps * spacing

    # Draw rows centered vertically
    y = center - total_h / 2
    for idx, (_text, _font, stroke_w, rgba) in enumerate(rows):
        layout, logical = layouts[idx]
        tx = center - logical.width / 2 - logical.x
        _draw_outlined_text(
            cr=cr,
            layout=layout,
            x=tx,
            y=y - logical.y,
            stroke_width=stroke_w,
            fill_rgba=rgba,
        )
        y += logical.height + spacing

        # AM/PM row right after time (first row)
        if idx == 0 and not is_24h:
            is_pm = now.tm_hour >= 12
            _draw_am_pm(cr=cr, size=size, y=y, font=am_pm_font, is_pm=is_pm)
            y += am_pm_height + spacing


def render_icon(
    size: int,
    now: time.struct_time,
    show_digital: bool,
    show_military: bool,
    show_date: bool,
) -> GdkPixbuf.Pixbuf | None:
    """Render clock icon in current mode."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    if show_digital:
        render_digital(
            cr=cr,
            size=size,
            now=now,
            is_24h=show_military,
            show_date=show_date,
        )
    else:
        render_analog(cr=cr, size=size, now=now)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
