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

"""Pure Cairo rendering for pet applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.pet.state import Mood, PetState, mood_color


def _draw_ears(cr: cairo.Context, size: int, mood: Mood) -> None:
    """Draw two pointed cat ears on top of the head."""
    cx = size / 2
    head_top = size * 0.28
    head_radius = size * 0.32
    ear_height = size * 0.22
    ear_half_w = size * 0.10

    red, green, blue = mood_color(mood=mood)

    for side in (-1, 1):
        ear_cx = cx + side * head_radius * 0.62
        ear_base_y = head_top + size * 0.04

        # Outer ear (body color, slightly darker)
        cr.move_to(ear_cx - ear_half_w, ear_base_y)
        cr.line_to(ear_cx + side * ear_half_w * 0.15, ear_base_y - ear_height)
        cr.line_to(ear_cx + ear_half_w, ear_base_y)
        cr.close_path()
        cr.set_source_rgb(red * 0.85, green * 0.85, blue * 0.85)
        cr.fill()

        # Inner ear (pink)
        inner_scale = 0.55
        inner_cx = ear_cx
        inner_base_y = ear_base_y - ear_height * 0.10
        cr.move_to(
            inner_cx - ear_half_w * inner_scale,
            inner_base_y,
        )
        cr.line_to(
            inner_cx + side * ear_half_w * 0.15 * inner_scale,
            inner_base_y - ear_height * inner_scale,
        )
        cr.line_to(
            inner_cx + ear_half_w * inner_scale,
            inner_base_y,
        )
        cr.close_path()
        cr.set_source_rgb(0.90, 0.60, 0.65)
        cr.fill()


def _draw_body(cr: cairo.Context, size: int, mood: Mood) -> None:
    """Draw the circular face."""
    cx = size / 2
    cy = size * 0.54
    radius = size * 0.32
    red, green, blue = mood_color(mood=mood)

    # Main circle
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgb(red, green, blue)
    cr.fill()

    # Subtle outline
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgba(0, 0, 0, 0.12)
    cr.set_line_width(max(1.0, size * 0.02))
    cr.stroke()

    # Upper-left highlight
    cr.save()
    cr.translate(cx - radius * 0.30, cy - radius * 0.30)
    cr.scale(radius * 0.25, radius * 0.18)
    cr.arc(0, 0, 1.0, 0, math.tau)
    cr.restore()
    cr.set_source_rgba(1, 1, 1, 0.30)
    cr.fill()


def _draw_eyes(cr: cairo.Context, size: int, mood: Mood) -> None:
    """Draw eyes that vary by mood."""
    cx = size / 2
    cy = size * 0.50
    eye_dx = size * 0.11
    eye_radius = size * 0.038
    dark = (0.12, 0.12, 0.16, 0.95)

    cr.set_source_rgba(*dark)

    if mood == Mood.SLEEPING:
        # Closed: short horizontal lines
        line_hw = size * 0.045
        cr.set_line_width(max(1.0, size * 0.025))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for dx in (-eye_dx, eye_dx):
            cr.move_to(cx + dx - line_hw, cy)
            cr.line_to(cx + dx + line_hw, cy)
            cr.stroke()
        return

    if mood == Mood.SLEEPY:
        # Nearly closed: thin slits
        slit_hw = size * 0.04
        cr.set_line_width(max(1.0, size * 0.02))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for dx in (-eye_dx, eye_dx):
            cr.move_to(cx + dx - slit_hw, cy + size * 0.005)
            cr.line_to(cx + dx + slit_hw, cy - size * 0.005)
            cr.stroke()
        return

    # Scale eyes by mood
    if mood == Mood.EXCITED:
        eye_radius *= 1.45
    elif mood == Mood.STRESSED:
        eye_radius *= 1.2
    elif mood == Mood.DROWSY:
        eye_radius *= 0.85

    # Half-lid clipping for focused/busy/drowsy
    needs_clip = mood in (Mood.FOCUSED, Mood.BUSY, Mood.DROWSY)
    if needs_clip:
        cr.save()
        if mood == Mood.DROWSY:
            # Droopy: show top portion only
            cr.rectangle(0, 0, size, cy + eye_radius * 0.4)
        else:
            # Focused/busy: upper lid, show bottom portion
            cr.rectangle(0, cy - eye_radius * 0.4, size, size)
        cr.clip()

    for dx in (-eye_dx, eye_dx):
        cr.arc(cx + dx, cy, eye_radius, 0, math.tau)
        cr.fill()

    if needs_clip:
        cr.restore()

    # Excited sparkle: tiny white dots in eyes
    if mood == Mood.EXCITED:
        cr.set_source_rgba(1, 1, 1, 0.9)
        sparkle_r = eye_radius * 0.28
        for dx in (-eye_dx, eye_dx):
            cr.arc(
                cx + dx - eye_radius * 0.25,
                cy - eye_radius * 0.25,
                sparkle_r,
                0,
                math.tau,
            )
            cr.fill()


def _draw_mouth(cr: cairo.Context, size: int, mood: Mood) -> None:
    """Draw mouth that varies by mood."""
    cx = size / 2
    mouth_y = size * 0.60
    dark = (0.12, 0.12, 0.16, 0.95)

    cr.set_source_rgba(*dark)
    cr.set_line_width(max(1.0, size * 0.025))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    if mood == Mood.SLEEPING:
        # Small "o" for snoring
        cr.arc(cx, mouth_y, size * 0.025, 0, math.tau)
        cr.stroke()
        return

    if mood == Mood.STRESSED:
        # Wavy/squiggle mouth
        wave_hw = size * 0.08
        cr.move_to(cx - wave_hw, mouth_y)
        cr.curve_to(
            cx - wave_hw * 0.5,
            mouth_y - size * 0.02,
            cx,
            mouth_y + size * 0.02,
            cx + wave_hw,
            mouth_y,
        )
        cr.stroke()
        return

    if mood in (Mood.FOCUSED, Mood.BUSY):
        # Flat line (neutral)
        half_w = size * 0.07
        cr.move_to(cx - half_w, mouth_y)
        cr.line_to(cx + half_w, mouth_y)
        cr.stroke()
        return

    # Smile or frown arc
    smile_map = {
        Mood.EXCITED: (size * 0.10, True),
        Mood.HAPPY: (size * 0.07, True),
        Mood.RELAXED: (size * 0.06, True),
        Mood.DROWSY: (size * 0.05, False),
        Mood.SLEEPY: (size * 0.04, False),
    }
    radius, is_smile = smile_map.get(mood, (size * 0.06, True))

    if is_smile:
        cr.arc(cx, mouth_y, radius, 0.3, math.pi - 0.3)
    else:
        cr.arc(cx, mouth_y + size * 0.025, radius, math.pi + 0.3, math.tau - 0.3)
    cr.stroke()


def _draw_whiskers(cr: cairo.Context, size: int) -> None:
    """Draw three whiskers on each side."""
    cx = size / 2
    cy = size * 0.57
    whisker_len = size * 0.14

    cr.set_source_rgba(0.2, 0.2, 0.2, 0.4)
    cr.set_line_width(max(0.8, size * 0.015))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    for side in (-1, 1):
        base_x = cx + side * size * 0.15
        for angle_offset in (-0.15, 0, 0.15):
            angle = angle_offset + (0 if side == 1 else math.pi)
            end_x = base_x + math.cos(angle) * whisker_len
            end_y = cy + math.sin(angle) * whisker_len
            cr.move_to(base_x, cy + angle_offset * size * 0.3)
            cr.line_to(end_x, end_y)
            cr.stroke()


def _draw_zzz(cr: cairo.Context, size: int) -> None:
    """Draw floating 'z' letters for sleeping mood."""
    cr.select_font_face("Sans", cairo.FONT_SLANT_ITALIC, cairo.FONT_WEIGHT_BOLD)
    cr.set_source_rgba(0.7, 0.7, 0.85, 0.8)

    base_x = size * 0.70
    base_y = size * 0.28
    for dx, dy, font_size in [
        (0, 0, size * 0.11),
        (size * 0.06, -size * 0.10, size * 0.08),
        (size * 0.10, -size * 0.17, size * 0.06),
    ]:
        cr.set_font_size(font_size)
        cr.move_to(base_x + dx, base_y + dy)
        cr.show_text("z")


def _draw_sweat_drop(cr: cairo.Context, size: int) -> None:
    """Draw a small sweat drop for stressed mood."""
    drop_x = size * 0.72
    drop_y = size * 0.38
    drop_h = size * 0.08

    cr.move_to(drop_x, drop_y)
    cr.curve_to(
        drop_x - size * 0.02,
        drop_y + drop_h * 0.5,
        drop_x - size * 0.025,
        drop_y + drop_h,
        drop_x,
        drop_y + drop_h,
    )
    cr.curve_to(
        drop_x + size * 0.025,
        drop_y + drop_h,
        drop_x + size * 0.02,
        drop_y + drop_h * 0.5,
        drop_x,
        drop_y,
    )
    cr.set_source_rgba(0.5, 0.7, 0.95, 0.7)
    cr.fill()


def render_icon(size: int, state: PetState) -> GdkPixbuf.Pixbuf | None:
    """Render the pet icon for the current state."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    _draw_ears(cr=cr, size=size, mood=state.mood)
    _draw_body(cr=cr, size=size, mood=state.mood)
    _draw_eyes(cr=cr, size=size, mood=state.mood)
    _draw_mouth(cr=cr, size=size, mood=state.mood)
    _draw_whiskers(cr=cr, size=size)

    if state.mood == Mood.SLEEPING:
        _draw_zzz(cr=cr, size=size)
    elif state.mood == Mood.STRESSED:
        _draw_sweat_drop(cr=cr, size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
