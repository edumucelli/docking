"""Rendering helpers for the Random Trivia applet icon."""

from __future__ import annotations

import math

import cairo

from docking.applets.draw import rounded_rect

from .state import TriviaEntry


def _draw_result_pill(*, cr: cairo.Context, size: int, correct: bool) -> None:
    pill_w = size * 0.24
    pill_h = size * 0.12
    pill_x = size * 0.5 - pill_w / 2
    pill_y = size * 0.82

    rounded_rect(
        cr=cr,
        x=pill_x,
        y=pill_y,
        width=pill_w,
        height=pill_h,
        radius=pill_h / 2,
    )
    if correct:
        cr.set_source_rgba(0.18, 0.72, 0.33, 0.98)
    else:
        cr.set_source_rgba(0.88, 0.24, 0.19, 0.98)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.7)
    cr.set_line_width(max(1.0, size * 0.02))
    cr.stroke()


def draw_trivia_icon(
    *,
    cr: cairo.Context,
    size: int,
    entry: TriviaEntry | None = None,
) -> None:
    card_x = size * 0.18
    card_y = size * 0.14
    card_w = size * 0.64
    card_h = size * 0.74

    rounded_rect(
        cr=cr,
        x=card_x,
        y=card_y,
        width=card_w,
        height=card_h,
        radius=size * 0.08,
    )
    cr.set_source_rgba(0.16, 0.42, 0.86, 0.98)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.9)
    cr.set_line_width(max(1.4, size * 0.04))
    cr.stroke()

    # Question mark
    cr.set_source_rgba(1, 1, 1, 0.96)
    cr.set_line_width(max(2.0, size * 0.065))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    cx = size * 0.5
    top_y = size * 0.30
    hook_y = size * 0.42
    stem_top_y = size * 0.47
    stem_bottom_y = size * 0.54

    cr.new_path()
    cr.move_to(cx - size * 0.11, top_y + size * 0.02)
    cr.curve_to(
        cx - size * 0.11,
        top_y - size * 0.04,
        cx - size * 0.02,
        top_y - size * 0.06,
        cx + size * 0.05,
        top_y - size * 0.01,
    )
    cr.curve_to(
        cx + size * 0.11,
        top_y + size * 0.03,
        cx + size * 0.08,
        hook_y - size * 0.01,
        cx,
        hook_y + size * 0.01,
    )
    cr.curve_to(
        cx - size * 0.02,
        hook_y + size * 0.01,
        cx - size * 0.01,
        stem_top_y - size * 0.01,
        cx,
        stem_top_y,
    )
    cr.stroke()

    cr.move_to(cx, stem_top_y)
    cr.line_to(cx, stem_bottom_y)
    cr.stroke()
    cr.arc(cx, size * 0.63, size * 0.025, 0, math.tau)
    cr.fill()

    # Accent lines
    cr.set_source_rgba(1, 1, 1, 0.25)
    cr.set_line_width(max(1.0, size * 0.02))
    for y in (0.77, 0.84):
        cr.move_to(card_x + size * 0.10, size * y)
        cr.line_to(card_x + card_w - size * 0.10, size * y)
        cr.stroke()

    if entry is not None and entry.selected_answer:
        _draw_result_pill(
            cr=cr,
            size=size,
            correct=entry.selected_answer == entry.correct_answer,
        )
