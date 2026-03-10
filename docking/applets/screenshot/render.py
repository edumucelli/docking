"""Rendering for screenshot applet icon."""

from __future__ import annotations

import cairo

from docking.applets.draw import rounded_rect


def _draw_soft_shadow(
    *,
    cr: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    r: float,
    dx: float,
    dy: float,
    spread: float,
    passes: int,
) -> None:
    """Approximate a blur shadow with multiple expanded rounded-rect fills."""
    base_alpha = 0.14
    for i in range(passes):
        t = (i + 1) / passes
        expand = spread * t
        alpha = base_alpha * (1.0 - t)
        cr.save()
        cr.set_source_rgba(0.0, 0.0, 0.0, alpha)
        rounded_rect(
            cr=cr,
            x=x - expand + dx,
            y=y - expand + dy,
            width=w + (2 * expand),
            height=h + (2 * expand),
            radius=r + expand,
        )
        cr.fill()
        cr.restore()


def _draw_screenshot_icon(*, cr: cairo.Context, size: int) -> None:
    tile_margin = 0.06 * size
    tile_size = size - (2 * tile_margin)
    tile_x = (size - tile_size) / 2.0
    tile_y = (size - tile_size) / 2.0
    tile_w = tile_size
    tile_h = tile_size
    tile_radius = 0.10 * tile_w

    # Soft drop shadow.
    _draw_soft_shadow(
        cr=cr,
        x=tile_x,
        y=tile_y,
        w=tile_w,
        h=tile_h,
        r=tile_radius,
        dx=0.0,
        dy=0.0,
        spread=0.04 * tile_w,
        passes=12,
    )

    # Main blue tile.
    rounded_rect(
        cr=cr, x=tile_x, y=tile_y, width=tile_w, height=tile_h, radius=tile_radius
    )
    cr.set_source_rgb(0x62 / 255.0, 0x8C / 255.0, 0xF6 / 255.0)
    cr.fill()

    # Inner dashed rounded rectangle.
    inset = 0.21 * tile_w
    inner_x = tile_x + inset
    inner_y = tile_y + inset
    inner_w = tile_w - (2 * inset)
    inner_h = inner_w
    inner_r = 0.09 * tile_w
    stroke_w = max(1.0, 0.032 * tile_w)

    # Second blue tone inside the crop contour.
    rounded_rect(
        cr=cr, x=inner_x, y=inner_y, width=inner_w, height=inner_h, radius=inner_r
    )
    cr.set_source_rgb(0x86 / 255.0, 0xA6 / 255.0, 0xF8 / 255.0)
    cr.fill()

    # Subtle lower-half opacity ramp across all blue areas.
    cr.save()
    rounded_rect(
        cr=cr, x=tile_x, y=tile_y, width=tile_w, height=tile_h, radius=tile_radius
    )
    cr.clip()
    mid_y = tile_y + (tile_h * 0.48)
    shade = cairo.LinearGradient(0, mid_y, 0, tile_y + tile_h)
    shade.add_color_stop_rgba(0.0, 0.0, 0.0, 0.0, 0.0)
    shade.add_color_stop_rgba(0.2, 0.0, 0.0, 0.0, 0.04)
    shade.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.10)
    cr.set_source(shade)
    cr.rectangle(tile_x, mid_y, tile_w, tile_h - (mid_y - tile_y))
    cr.fill()
    cr.restore()

    cr.set_source_rgba(1.0, 1.0, 1.0, 0.92)
    cr.set_line_width(stroke_w)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    dash_len = stroke_w * 2.0
    cr.set_dash([dash_len, dash_len], 0)
    rounded_rect(
        cr=cr, x=inner_x, y=inner_y, width=inner_w, height=inner_h, radius=inner_r
    )
    cr.stroke()
    cr.set_dash([])

    # Bottom-right solid "+" marker at the contour edge intersection.
    offset = stroke_w * 0.55
    cross_x = inner_x + inner_w + (stroke_w / 2.0) - offset
    cross_y = inner_y + inner_h + (stroke_w / 2.0) - offset
    cross_len = 0.14 * tile_w
    half = cross_len / 2.0

    cr.set_line_cap(cairo.LINE_CAP_BUTT)
    cr.set_line_join(cairo.LINE_JOIN_MITER)
    cr.move_to(cross_x - half, cross_y)
    cr.line_to(cross_x + half, cross_y)
    cr.stroke()

    cr.move_to(cross_x, cross_y - half)
    cr.line_to(cross_x, cross_y + half)
    cr.stroke()
