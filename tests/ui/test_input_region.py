"""Tests for input region computation.

Input region uses two-state model: content rect when dock is visible/animating,
trigger strip only when fully hidden. No interpolation during animation to
prevent oscillation from mouse re-entering a shrinking region.
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


class TestAutohideVisible:
    """When visible, content rect."""

    def test_bottom_content_rect(self):
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.VISIBLE,
        )
        assert h == CONTENT_CROSS
        assert y == WIN_H - CONTENT_CROSS


class TestAutohideHiding:
    """During HIDING animation, keep content rect (prevents oscillation)."""

    def test_keeps_content_rect(self):
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.HIDING,
        )
        # Still content-sized, NOT shrunk
        assert h == CONTENT_CROSS

    def test_same_as_visible(self):
        visible = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.VISIBLE,
        )
        hiding = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.HIDING,
        )
        assert visible == hiding


class TestAutohideShowing:
    """During SHOWING animation, keep content rect."""

    def test_keeps_content_rect(self):
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.SHOWING,
        )
        assert h == CONTENT_CROSS


class TestAutohideHidden:
    """When fully hidden, trigger strip at edge."""

    def test_bottom_trigger_strip(self):
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.HIDDEN,
        )
        assert h == TRIGGER_PX
        assert y == WIN_H - TRIGGER_PX

    def test_top_trigger_wider(self):
        x, y, w, h = compute_input_rect(
            Position.TOP,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.HIDDEN,
        )
        assert h == TRIGGER_PX_TOP
        assert y == 0


class TestHeadroomExcluded:
    """Headroom above icons must NOT be in the input region."""

    def test_bottom_headroom_excluded(self):
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.VISIBLE,
        )
        assert y > 0
        assert y == WIN_H - CONTENT_CROSS
        assert h == CONTENT_CROSS
