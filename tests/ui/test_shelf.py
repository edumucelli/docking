"""Tests for shelf background drawing."""

from __future__ import annotations

import cairo

from docking.core.theme import Theme
from docking.ui.shelf import draw_shelf_background, rounded_rect


def _context(width: int = 240, height: int = 120) -> cairo.Context:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    return cairo.Context(surface)


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
