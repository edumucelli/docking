"""Tests for shelf background drawing."""

from __future__ import annotations

import cairo

from docking.core.theme import Theme
from docking.ui.shelf import clip_shelf_background, draw_shelf_background, rounded_rect


def _context(width: int = 240, height: int = 120) -> cairo.Context:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    return cairo.Context(surface)


def _alpha_at(surface: cairo.ImageSurface, x: int, y: int) -> int:
    surface.flush()
    data = surface.get_data()
    return data[y * surface.get_stride() + x * 4 + 3]


class TestRoundedRect:
    def test_draws_with_rounded_bottom(self):
        # Given
        cr = _context()
        # When
        rounded_rect(cr, x=10, y=10, width=100, height=50, radius=8, round_bottom=True)
        x1, y1, x2, y2 = cr.path_extents()
        # Then
        assert x2 > x1
        assert y2 > y1

    def test_draws_with_square_bottom(self):
        # Given
        cr = _context()
        # When
        rounded_rect(
            cr,
            x=12,
            y=8,
            width=90,
            height=42,
            radius=6,
            round_bottom=False,
        )
        x1, y1, x2, y2 = cr.path_extents()
        # Then
        assert x2 > x1
        assert y2 > y1

    def test_clamps_large_radius_to_shape_bounds(self):
        # Given
        cr = _context()
        # When
        rounded_rect(
            cr,
            x=20,
            y=18,
            width=80,
            height=24,
            radius=999,
            round_bottom=True,
        )
        x1, y1, x2, y2 = cr.path_extents()
        # Then
        assert x1 >= 20
        assert y1 >= 18
        assert x2 <= 100
        assert y2 <= 42


class TestShelfBackground:
    def test_draws_full_shelf_background(self):
        # Given
        cr = _context()
        theme = Theme.load("default", 48)
        # When
        draw_shelf_background(cr=cr, x=0, y=0, w=220, h=60, theme=theme)
        # Then
        # Function should render without errors for normal dimensions.
        assert True

    def test_draws_even_when_height_is_zero(self):
        # Given
        cr = _context()
        theme = Theme.load("default", 48)
        # When
        draw_shelf_background(cr=cr, x=0, y=0, w=180, h=0, theme=theme)
        # Then
        # Covers the h==0 fallback branch for gradient stop calculations.
        assert True


class TestShelfClip:
    def test_clips_square_fill_to_pill_corners(self):
        # Given
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 160, 60)
        cr = cairo.Context(surface)
        theme = Theme.load("pill", 48)

        # When
        cr.save()
        assert clip_shelf_background(cr=cr, x=10, y=10, w=120, h=30, theme=theme)
        cr.set_source_rgba(1, 0, 0, 1)
        cr.rectangle(0, 0, 160, 60)
        cr.fill()
        cr.restore()

        # Then
        assert _alpha_at(surface, 10, 10) == 0
        assert _alpha_at(surface, 70, 25) == 255
