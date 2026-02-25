"""Tests for input region computation -- continuous animation model.

The input region is interpolated using hide_offset (0.0=visible, 1.0=hidden)
matching Plank's approach. No abrupt region changes at state boundaries.
"""

import sys
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.core.position import Position  # noqa: E402
from docking.ui.autohide import HideState  # noqa: E402
from docking.ui.dock_window import (  # noqa: E402
    compute_input_rect,
    TRIGGER_PX,
    TRIGGER_PX_TOP,
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
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            None,
        )
        assert w == CONTENT_W
        assert x == CONTENT_OFFSET
        assert h == CONTENT_CROSS
        assert y == WIN_H - CONTENT_CROSS

    def test_top_content_rect(self):
        x, y, w, h = compute_input_rect(
            Position.TOP,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            None,
        )
        assert y == 0
        assert h == CONTENT_CROSS

    def test_left_content_rect(self):
        x, y, w, h = compute_input_rect(
            Position.LEFT,
            WIN_H,
            WIN_W,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            None,
        )
        assert x == 0
        assert w == CONTENT_CROSS


class TestAutohideVisible:
    """When autohide is on and dock fully visible (hide_offset=0.0)."""

    def test_bottom_content_rect(self):
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.VISIBLE,
            hide_offset=0.0,
        )
        assert w == CONTENT_W
        assert h == CONTENT_CROSS
        assert y == WIN_H - CONTENT_CROSS

    def test_cross_excludes_headroom(self):
        # Headroom above icons must NOT be interactive
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.VISIBLE,
            hide_offset=0.0,
        )
        assert y > 0
        assert y == WIN_H - CONTENT_CROSS


class TestAutohideHidden:
    """When dock fully hidden (hide_offset=1.0), trigger strip at edge."""

    def test_bottom_trigger_strip(self):
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.HIDDEN,
            hide_offset=1.0,
        )
        assert h == TRIGGER_PX
        assert y == WIN_H - TRIGGER_PX
        assert w == CONTENT_W

    def test_top_trigger_wider(self):
        x, y, w, h = compute_input_rect(
            Position.TOP,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.HIDDEN,
            hide_offset=1.0,
        )
        assert h == TRIGGER_PX_TOP
        assert y == 0


class TestContinuousAnimation:
    """Input region interpolates smoothly between visible and hidden."""

    def test_halfway_cross_is_between(self):
        # At 50% hidden, cross should be between trigger and content
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.HIDING,
            hide_offset=0.5,
        )
        assert TRIGGER_PX < h < CONTENT_CROSS

    def test_cross_decreases_monotonically(self):
        # As hide_offset increases, cross should decrease
        prev_h = CONTENT_CROSS + 1
        for offset in [0.0, 0.25, 0.5, 0.75, 1.0]:
            _, _, _, h = compute_input_rect(
                Position.BOTTOM,
                WIN_W,
                WIN_H,
                CONTENT_OFFSET,
                CONTENT_W,
                CONTENT_CROSS,
                HideState.HIDING,
                hide_offset=offset,
            )
            assert h <= prev_h
            prev_h = h

    def test_showing_same_as_hiding_at_same_offset(self):
        # SHOWING and HIDING at the same hide_offset produce same region
        showing = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.SHOWING,
            hide_offset=0.3,
        )
        hiding = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.HIDING,
            hide_offset=0.3,
        )
        assert showing == hiding
