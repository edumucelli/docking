"""Tests for shared Cairo overlay helpers."""

from __future__ import annotations

import cairo

from docking.ui.overlays import draw_circle_badge, draw_count_badge, draw_progress_bar


def _context(size: int = 64) -> tuple[cairo.ImageSurface, cairo.Context]:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    return surface, cairo.Context(surface)


def _non_empty(surface: cairo.ImageSurface) -> bool:
    surface.flush()
    return any(surface.get_data())


def test_draw_circle_badge_with_and_without_outline():
    surface, cr = _context()
    draw_circle_badge(
        cr=cr,
        cx=16,
        cy=16,
        radius=8,
        background_rgba=(1, 0, 0, 1),
    )
    draw_circle_badge(
        cr=cr,
        cx=40,
        cy=16,
        radius=8,
        background_rgba=(0, 1, 0, 1),
        outline_rgba=(1, 1, 1, 1),
        outline_width=2,
    )

    assert _non_empty(surface)


def test_draw_count_badge_supports_all_label_lengths():
    surface, cr = _context()

    draw_count_badge(cr=cr, x=2, y=4, width=18, height=16, badge_count=7)
    draw_count_badge(cr=cr, x=22, y=4, width=20, height=16, badge_count=42)
    draw_count_badge(cr=cr, x=2, y=28, width=28, height=16, badge_count=120)

    assert _non_empty(surface)


def test_draw_progress_bar_clamps_and_switches_dark_fill():
    surface, cr = _context()

    draw_progress_bar(
        cr=cr,
        x=4,
        y=8,
        width=40,
        height=8,
        progress=0,
        color=(0.8, 0.8, 0.8),
    )
    draw_progress_bar(
        cr=cr,
        x=4,
        y=24,
        width=40,
        height=8,
        progress=0.5,
        color=(0.1, 0.1, 0.1),
    )
    draw_progress_bar(
        cr=cr,
        x=4,
        y=40,
        width=40,
        height=8,
        progress=2,
        color=(0.8, 0.2, 0.2, 1.0),
    )

    assert _non_empty(surface)
