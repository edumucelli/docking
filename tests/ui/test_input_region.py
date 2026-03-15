"""Tests for input region computation.

Input region uses two-state model: content rect when dock is visible/animating,
trigger strip only when fully hidden. No interpolation during animation to
prevent oscillation from mouse re-entering a shrinking region.
"""

import sys
from unittest.mock import MagicMock

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.core.position import Position
from docking.ui.autohide import HideState
from docking.ui.geometry import (
    TRIGGER_PX,
    TRIGGER_PX_TOP,
    compute_input_rect,
)

WIN_W = 1920
WIN_H = 120
CONTENT_OFFSET = 700
CONTENT_W = 520
CONTENT_CROSS = 53


class TestAutohideOff:
    """When autohide is off, region is content-only."""

    def test_bottom_content_rect(self):
        x, y, w, h = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=None,
        )
        assert w == CONTENT_W
        assert x == CONTENT_OFFSET
        assert h == CONTENT_CROSS
        assert y == WIN_H - CONTENT_CROSS


class TestAutohideVisible:
    """When visible, content rect."""

    def test_bottom_content_rect(self):
        x, y, w, h = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.VISIBLE,
        )
        assert h == CONTENT_CROSS
        assert y == WIN_H - CONTENT_CROSS


class TestAutohideHiding:
    """During HIDING animation, keep content rect (prevents oscillation)."""

    def test_keeps_content_rect(self):
        x, y, w, h = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.HIDING,
        )
        # Still content-sized, NOT shrunk
        assert h == CONTENT_CROSS

    def test_same_as_visible(self):
        visible = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.VISIBLE,
        )
        hiding = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.HIDING,
        )
        assert visible == hiding


class TestAutohideShowing:
    """During SHOWING animation, keep content rect."""

    def test_keeps_content_rect(self):
        x, y, w, h = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.SHOWING,
        )
        assert h == CONTENT_CROSS


class TestAutohideHidden:
    """When fully hidden, trigger strip at edge."""

    def test_bottom_trigger_strip(self):
        x, y, w, h = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.HIDDEN,
        )
        assert h == TRIGGER_PX
        assert y == WIN_H - TRIGGER_PX

    def test_top_trigger_wider(self):
        x, y, w, h = compute_input_rect(
            pos=Position.TOP,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.HIDDEN,
        )
        assert h == TRIGGER_PX_TOP
        assert y == 0


class TestHeadroomExcluded:
    """Headroom above icons must NOT be in the input region."""

    def test_bottom_headroom_excluded(self):
        x, y, w, h = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.VISIBLE,
        )
        assert y > 0
        assert y == WIN_H - CONTENT_CROSS
        assert h == CONTENT_CROSS


GAP = 6


class TestDistanceFromEdgeHidden:
    """When hidden with distance_from_edge, trigger strip extends through gap."""

    def test_bottom_trigger_includes_gap(self):
        x, y, w, h = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.HIDDEN,
            distance_from_edge=GAP,
        )
        assert h == TRIGGER_PX + GAP
        assert y == WIN_H - h

    def test_top_trigger_includes_gap(self):
        x, y, w, h = compute_input_rect(
            pos=Position.TOP,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.HIDDEN,
            distance_from_edge=GAP,
        )
        assert h == TRIGGER_PX_TOP + GAP
        assert y == 0

    def test_left_trigger_includes_gap(self):
        x, y, w, h = compute_input_rect(
            pos=Position.LEFT,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.HIDDEN,
            distance_from_edge=GAP,
        )
        assert w == TRIGGER_PX + GAP
        assert x == 0

    def test_right_trigger_includes_gap(self):
        x, y, w, h = compute_input_rect(
            pos=Position.RIGHT,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.HIDDEN,
            distance_from_edge=GAP,
        )
        assert w == TRIGGER_PX + GAP
        assert x == WIN_W - w


class TestDistanceFromEdgeVisible:
    """When visible with distance_from_edge, content rect extends through gap."""

    def test_bottom_content_includes_gap(self):
        x, y, w, h = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=None,
            distance_from_edge=GAP,
        )
        assert h == CONTENT_CROSS + GAP
        assert y == WIN_H - h

    def test_top_content_includes_gap(self):
        x, y, w, h = compute_input_rect(
            pos=Position.TOP,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=None,
            distance_from_edge=GAP,
        )
        assert h == CONTENT_CROSS + GAP
        assert y == 0

    def test_left_content_includes_gap(self):
        x, y, w, h = compute_input_rect(
            pos=Position.LEFT,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=None,
            distance_from_edge=GAP,
        )
        assert w == CONTENT_CROSS + GAP
        assert x == 0

    def test_zero_gap_matches_original(self):
        """distance_from_edge=0 produces identical result to omitting it."""
        without = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.HIDDEN,
        )
        with_zero = compute_input_rect(
            pos=Position.BOTTOM,
            window_w=WIN_W,
            window_h=WIN_H,
            content_offset=CONTENT_OFFSET,
            content_w=CONTENT_W,
            content_cross=CONTENT_CROSS,
            autohide_state=HideState.HIDDEN,
            distance_from_edge=0,
        )
        assert without == with_zero
