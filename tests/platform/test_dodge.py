"""Tests for window-dock overlap detection geometry."""

from docking.platform.backends.x11.impl.dodge import ScreenRect, rects_overlap


class TestRectsOverlap:
    """Pure geometry overlap checks."""

    def test_overlapping_rects(self):
        # Given two rects that share interior pixels
        a = ScreenRect(x=0, y=0, width=100, height=100)
        b = ScreenRect(x=50, y=50, width=100, height=100)
        # Then they overlap
        assert rects_overlap(a=a, b=b) is True

    def test_adjacent_touching_edge_is_not_overlap(self):
        # Given two rects that share an edge but no interior
        a = ScreenRect(x=0, y=0, width=100, height=100)
        b = ScreenRect(x=100, y=0, width=100, height=100)
        # Then they do not overlap (half-open intervals)
        assert rects_overlap(a=a, b=b) is False

    def test_adjacent_touching_bottom_edge(self):
        a = ScreenRect(x=0, y=0, width=100, height=100)
        b = ScreenRect(x=0, y=100, width=100, height=100)
        assert rects_overlap(a=a, b=b) is False

    def test_non_overlapping_distant(self):
        # Given two rects far apart
        a = ScreenRect(x=0, y=0, width=50, height=50)
        b = ScreenRect(x=200, y=200, width=50, height=50)
        # Then they do not overlap
        assert rects_overlap(a=a, b=b) is False

    def test_contained_rect(self):
        # Given a small rect fully inside a large rect
        outer = ScreenRect(x=0, y=0, width=200, height=200)
        inner = ScreenRect(x=50, y=50, width=20, height=20)
        # Then they overlap
        assert rects_overlap(a=outer, b=inner) is True
        assert rects_overlap(a=inner, b=outer) is True

    def test_zero_width_at_edge(self):
        # Given a zero-width rect at the left edge of another
        a = ScreenRect(x=0, y=0, width=0, height=100)
        b = ScreenRect(x=0, y=0, width=200, height=200)
        # Then no overlap (a.x + a.width == 0, not > b.x == 0)
        assert rects_overlap(a=a, b=b) is False

    def test_zero_height_at_edge(self):
        a = ScreenRect(x=0, y=0, width=100, height=0)
        b = ScreenRect(x=0, y=0, width=200, height=200)
        assert rects_overlap(a=a, b=b) is False

    def test_symmetry(self):
        a = ScreenRect(x=10, y=10, width=50, height=50)
        b = ScreenRect(x=30, y=30, width=50, height=50)
        assert rects_overlap(a=a, b=b) == rects_overlap(a=b, b=a)
