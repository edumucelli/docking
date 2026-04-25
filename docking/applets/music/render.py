"""Pure Cairo rendering for music applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.draw import rounded_rect
from docking.ui.overlays import draw_circle_badge

_NO_PLAYER_ICON_CACHE: dict[int, GdkPixbuf.Pixbuf] = {}


def _crop_center_square(pixbuf: GdkPixbuf.Pixbuf, size: int) -> GdkPixbuf.Pixbuf | None:
    w = pixbuf.get_width()
    h = pixbuf.get_height()
    if w <= 0 or h <= 0:
        return None
    scale = max(size / w, size / h)
    scaled_w = max(size, round(w * scale))
    scaled_h = max(size, round(h * scale))
    scaled = pixbuf.scale_simple(scaled_w, scaled_h, GdkPixbuf.InterpType.BILINEAR)
    if scaled is None:
        return None
    src_x = max(0, (scaled_w - size) // 2)
    src_y = max(0, (scaled_h - size) // 2)
    canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    if canvas is None:
        return None
    canvas.fill(0x00000000)
    scaled.copy_area(src_x, src_y, size, size, canvas, 0, 0)
    return canvas


def _draw_album_art(cr: cairo.Context, size: int, art: GdkPixbuf.Pixbuf) -> None:
    margin = size * 0.08
    tile_size = round(size - 2 * margin)
    cropped = _crop_center_square(pixbuf=art, size=tile_size)
    if cropped is None:
        return
    x = margin
    y = margin
    radius = tile_size * 0.14

    rounded_rect(cr=cr, x=x, y=y, width=tile_size, height=tile_size, radius=radius)
    cr.clip()
    Gdk.cairo_set_source_pixbuf(cr, cropped, x, y)
    cr.paint()
    cr.reset_clip()

    rounded_rect(cr=cr, x=x, y=y, width=tile_size, height=tile_size, radius=radius)
    cr.set_source_rgba(1, 1, 1, 0.20)
    cr.set_line_width(max(1.0, size * 0.02))
    cr.stroke()


def _draw_idle_music_tile_vector(cr: cairo.Context, size: int) -> None:
    """Draw the yellow split music tile used for non-playing states."""
    top_h = size * 0.767
    bottom_h = size - top_h
    split_x = size * 0.3535

    # Top yellow panel (matched to target palette).
    cr.rectangle(0, 0, split_x, top_h)
    cr.set_source_rgb(253 / 255.0, 249 / 255.0, 165 / 255.0)
    cr.fill()
    cr.rectangle(split_x, 0, size - split_x, top_h)
    cr.set_source_rgb(251 / 255.0, 242 / 255.0, 74 / 255.0)
    cr.fill()

    # Stylized note (filled geometry to avoid stroke artifacts at small sizes).
    note_dark = (36 / 255.0, 36 / 255.0, 36 / 255.0)
    note_gray = (67 / 255.0, 67 / 255.0, 67 / 255.0)
    note_w = size * 0.066
    half_w = note_w / 2.0

    left_stem_x = size * 0.47
    left_stem_top = top_h * 0.30
    left_stem_bottom = top_h * 0.691
    right_stem_x = size * 0.73
    right_stem_top = top_h * 0.23
    right_stem_bottom = top_h * 0.612

    cr.set_source_rgb(*note_dark)
    cr.rectangle(
        left_stem_x - half_w,
        left_stem_top,
        note_w,
        max(1.0, left_stem_bottom - left_stem_top),
    )
    cr.fill()

    cr.rectangle(
        right_stem_x - half_w,
        right_stem_top,
        note_w,
        max(1.0, right_stem_bottom - right_stem_top),
    )
    cr.fill()

    # Beam drawn as a true-thickness quad (perpendicular to slope), slightly
    # thicker to counter visual thinning after downscaling.
    beam_x0 = left_stem_x
    beam_y0 = left_stem_top + half_w
    beam_x1 = right_stem_x
    beam_y1 = right_stem_top + half_w
    beam_dx = beam_x1 - beam_x0
    beam_dy = beam_y1 - beam_y0
    beam_len = max(1e-6, math.hypot(beam_dx, beam_dy))
    norm_x = -beam_dy / beam_len
    norm_y = beam_dx / beam_len
    beam_thickness = note_w * 1.18
    off_x = norm_x * (beam_thickness / 2.0)
    off_y = norm_y * (beam_thickness / 2.0)

    cr.move_to(beam_x0 - off_x, beam_y0 - off_y)
    cr.line_to(beam_x0 + off_x, beam_y0 + off_y)
    cr.line_to(beam_x1 + off_x, beam_y1 + off_y)
    cr.line_to(beam_x1 - off_x, beam_y1 - off_y)
    cr.close_path()
    cr.fill()

    cr.arc(size * 0.408, top_h * 0.74, size * 0.088, 0, 2 * math.pi)
    cr.set_source_rgb(*note_gray)
    cr.fill()
    cr.arc(size * 0.669, top_h * 0.66, size * 0.084, 0, 2 * math.pi)
    cr.set_source_rgb(*note_dark)
    cr.fill()

    # Bottom transport strip.
    cr.rectangle(0, top_h, split_x, bottom_h)
    cr.set_source_rgb(192 / 255.0, 192 / 255.0, 192 / 255.0)
    cr.fill()
    cr.rectangle(split_x, top_h, size - split_x, bottom_h)
    cr.set_source_rgb(130 / 255.0, 130 / 255.0, 130 / 255.0)
    cr.fill()

    button = max(2.0, size * 0.06)
    by = top_h + (bottom_h - button) / 2
    cr.set_source_rgb(67 / 255.0, 67 / 255.0, 67 / 255.0)
    cr.rectangle(size * 0.09, by, button, button)
    cr.rectangle(size * 0.20, by, button, button)
    cr.fill()

    bar_h = max(2.0, size * 0.06)
    bar_w = size * 0.42
    bx = size * 0.47
    by2 = top_h + (bottom_h - bar_h) / 2
    cr.rectangle(bx, by2, bar_w, bar_h)
    cr.set_source_rgb(36 / 255.0, 36 / 255.0, 36 / 255.0)
    cr.fill()


def _idle_music_tile_pixbuf(size: int) -> GdkPixbuf.Pixbuf | None:
    cached = _NO_PLAYER_ICON_CACHE.get(size)
    if cached is not None:
        return cached

    master_size = 512
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, master_size, master_size)
    cr = cairo.Context(surface)
    _draw_idle_music_tile_vector(cr=cr, size=master_size)
    pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, master_size, master_size)
    if pixbuf is None:
        return None
    if size != master_size:
        scaled = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        if scaled is not None:
            pixbuf = scaled
    _NO_PLAYER_ICON_CACHE[size] = pixbuf
    return pixbuf


def _draw_idle_music_tile(cr: cairo.Context, size: int) -> None:
    tile_margin = size * 0.06
    tile_size = round(size - (2 * tile_margin))
    tile_x = (size - tile_size) / 2.0
    tile_y = (size - tile_size) / 2.0
    tile_radius = tile_size * 0.10

    # Soft shadow to match rounded-tile applets.
    rounded_rect(
        cr=cr,
        x=tile_x + (size * 0.01),
        y=tile_y + (size * 0.018),
        width=tile_size,
        height=tile_size,
        radius=tile_radius,
    )
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.16)
    cr.fill()

    pixbuf = _idle_music_tile_pixbuf(size=tile_size)
    if pixbuf is None:
        rounded_rect(
            cr=cr,
            x=tile_x,
            y=tile_y,
            width=tile_size,
            height=tile_size,
            radius=tile_radius,
        )
        cr.save()
        cr.clip()
        cr.translate(tile_x, tile_y)
        _draw_idle_music_tile_vector(cr=cr, size=tile_size)
        cr.restore()
        cr.reset_clip()
        return

    rounded_rect(
        cr=cr,
        x=tile_x,
        y=tile_y,
        width=tile_size,
        height=tile_size,
        radius=tile_radius,
    )
    cr.clip()
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, tile_x, tile_y)
    cr.paint()
    cr.reset_clip()


def _draw_fallback_music_glyph(cr: cairo.Context, size: int) -> None:
    # Background tile.
    margin = size * 0.08
    x = margin
    y = margin
    w = size - 2 * margin
    h = w
    rounded_rect(cr=cr, x=x, y=y, width=w, height=h, radius=w * 0.16)
    grad = cairo.LinearGradient(x, y, x + w, y + h)
    grad.add_color_stop_rgb(0.0, 0.22, 0.29, 0.50)
    grad.add_color_stop_rgb(1.0, 0.08, 0.12, 0.27)
    cr.set_source(grad)
    cr.fill()

    # Music note.
    cr.set_source_rgba(1, 1, 1, 0.92)
    cr.set_line_width(max(1.4, size * 0.06))
    stem_x = size * 0.58
    stem_top = size * 0.28
    stem_bottom = size * 0.64
    cr.move_to(stem_x, stem_top)
    cr.line_to(stem_x, stem_bottom)
    cr.stroke()

    cr.arc(size * 0.48, size * 0.67, size * 0.10, 0, 2 * math.pi)
    cr.fill()

    cr.arc(size * 0.66, size * 0.60, size * 0.09, 0, 2 * math.pi)
    cr.fill()

    cr.set_line_width(max(1.4, size * 0.055))
    cr.move_to(stem_x, stem_top)
    cr.line_to(size * 0.74, size * 0.24)
    cr.stroke()


def _volume_step_count(volume_percent: int) -> int:
    volume = max(0, min(100, int(volume_percent)))
    if volume <= 0:
        return 0
    if volume <= 25:
        return 1
    if volume <= 50:
        return 2
    if volume <= 75:
        return 3
    return 4


def _draw_volume_badge(cr: cairo.Context, size: int, volume_percent: int) -> None:
    radius = size * 0.16
    cx = radius + size * 0.06
    cy = size - radius - size * 0.06

    draw_circle_badge(
        cr=cr,
        cx=cx,
        cy=cy,
        radius=radius,
        background_rgba=(0.20, 0.20, 0.20, 0.95),
    )

    cr.set_source_rgba(1, 1, 1, 0.95)
    # Keep the speaker tiny so arc levels are the primary visual cue.
    body_w = size * 0.022
    body_h = size * 0.042
    body_x = cx - size * 0.090
    body_y = cy - body_h / 2
    rounded_rect(
        cr=cr,
        x=body_x,
        y=body_y,
        width=body_w,
        height=body_h,
        radius=body_w * 0.45,
    )
    cr.fill()

    horn_w = size * 0.030
    horn_h = size * 0.055
    horn_x = body_x + body_w
    rounded_rect(
        cr=cr,
        x=horn_x,
        y=cy - horn_h / 2,
        width=horn_w,
        height=horn_h,
        radius=horn_h * 0.40,
    )
    cr.fill()

    level = _volume_step_count(volume_percent=volume_percent)
    if level <= 0:
        return

    cr.set_line_width(max(1.0, size * 0.016))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    arc_center_x = horn_x + horn_w - size * 0.002
    start = -0.82
    end = 0.82
    for step in range(level):
        arc_radius = size * (0.044 + 0.022 * step)
        cr.arc(arc_center_x, cy, arc_radius, start, end)
        cr.stroke()


def _draw_status_badge(cr: cairo.Context, size: int, playback_status: str) -> None:
    radius = size * 0.16
    cx = size - radius - size * 0.06
    cy = size - radius - size * 0.06

    if playback_status == "Playing":
        color = (0.18, 0.69, 0.39)
    elif playback_status == "Paused":
        color = (0.87, 0.63, 0.15)
    else:
        color = (0.42, 0.42, 0.42)

    draw_circle_badge(
        cr=cr,
        cx=cx,
        cy=cy,
        radius=radius,
        background_rgba=(*color, 0.95),
    )

    cr.set_source_rgba(1, 1, 1, 0.95)
    if playback_status == "Paused":
        bar_w = size * 0.04
        bar_h = size * 0.11
        gap = size * 0.025
        x0 = cx - gap / 2 - bar_w
        y0 = cy - bar_h / 2
        cr.rectangle(x0, y0, bar_w, bar_h)
        cr.rectangle(x0 + bar_w + gap, y0, bar_w, bar_h)
        cr.fill()
        return

    if playback_status == "Playing":
        cr.move_to(cx - size * 0.035, cy - size * 0.055)
        cr.line_to(cx + size * 0.055, cy)
        cr.line_to(cx - size * 0.035, cy + size * 0.055)
        cr.close_path()
        cr.fill()
        return

    side = size * 0.09
    cr.rectangle(cx - side / 2, cy - side / 2, side, side)
    cr.fill()


def create_music_icon(
    *,
    size: int,
    playback_status: str,
    album_art: GdkPixbuf.Pixbuf | None,
    volume_percent: int = 0,
    available: bool = True,
) -> GdkPixbuf.Pixbuf | None:
    """Render music icon using album art/fallback and status badge."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()

    if not available:
        _draw_idle_music_tile(cr=cr, size=size)
    elif album_art is not None:
        _draw_album_art(cr=cr, size=size, art=album_art)
    else:
        _draw_fallback_music_glyph(cr=cr, size=size)

    if available:
        _draw_volume_badge(cr=cr, size=size, volume_percent=volume_percent)
        _draw_status_badge(cr=cr, size=size, playback_status=playback_status)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
