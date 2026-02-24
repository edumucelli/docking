"""Tests for input region computation -- regression guard for tooltip/zoom snap."""

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
CONTENT_CROSS = 53  # icon_size(48) + bottom_padding(~5)


class TestVisibleContentOnly:
    """When autohide is VISIBLE or off, input region must be content-only.

    Using full-window region causes tooltip popups to trigger spurious
    crossing events, making icons snap/de-zoom when the tooltip appears.
    """

    def test_autohide_visible_bottom_is_content_only(self):
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.VISIBLE,
        )
        assert w == CONTENT_W
        assert x == CONTENT_OFFSET

    def test_autohide_visible_bottom_cross_is_icon_area(self):
        # Cross extent must be content_cross, not full window height --
        # headroom above icons must NOT be interactive.
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
        assert y == WIN_H - CONTENT_CROSS  # at screen edge

    def test_autohide_visible_top_at_screen_edge(self):
        x, y, w, h = compute_input_rect(
            Position.TOP,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.VISIBLE,
        )
        assert h == CONTENT_CROSS
        assert y == 0

    def test_autohide_visible_left_at_screen_edge(self):
        x, y, w, h = compute_input_rect(
            Position.LEFT,
            WIN_H,
            WIN_W,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.VISIBLE,
        )
        assert w == CONTENT_CROSS
        assert x == 0

    def test_autohide_visible_right_at_screen_edge(self):
        x, y, w, h = compute_input_rect(
            Position.RIGHT,
            WIN_H,
            WIN_W,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.VISIBLE,
        )
        assert w == CONTENT_CROSS
        assert x == WIN_H - CONTENT_CROSS

    def test_autohide_off_is_content_only(self):
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
        assert h == CONTENT_CROSS


class TestShowingFullWindow:
    """During SHOWING animation, use full window to prevent oscillation."""

    def test_showing_uses_full_window(self):
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.SHOWING,
        )
        assert (x, y, w, h) == (0, 0, WIN_W, WIN_H)

    def test_showing_full_window_all_positions(self):
        for pos in Position:
            x, y, w, h = compute_input_rect(
                pos,
                WIN_W,
                WIN_H,
                CONTENT_OFFSET,
                CONTENT_W,
                CONTENT_CROSS,
                HideState.SHOWING,
            )
            assert (x, y, w, h) == (0, 0, WIN_W, WIN_H)


class TestHiddenTriggerStrip:
    """When hidden, a thin trigger strip at the screen edge."""

    def test_bottom_trigger_at_bottom_edge(self):
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
        assert w == WIN_W

    def test_top_trigger_at_top_edge_wider(self):
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

    def test_left_trigger_at_left_edge(self):
        x, y, w, h = compute_input_rect(
            Position.LEFT,
            WIN_H,
            WIN_W,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.HIDDEN,
        )
        assert w == TRIGGER_PX
        assert x == 0

    def test_right_trigger_at_right_edge(self):
        x, y, w, h = compute_input_rect(
            Position.RIGHT,
            WIN_H,
            WIN_W,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.HIDDEN,
        )
        assert w == TRIGGER_PX
        assert x == WIN_H - TRIGGER_PX

    def test_hiding_same_as_hidden(self):
        for pos in Position:
            hidden = compute_input_rect(
                pos,
                WIN_W,
                WIN_H,
                CONTENT_OFFSET,
                CONTENT_W,
                CONTENT_CROSS,
                HideState.HIDDEN,
            )
            hiding = compute_input_rect(
                pos,
                WIN_W,
                WIN_H,
                CONTENT_OFFSET,
                CONTENT_W,
                CONTENT_CROSS,
                HideState.HIDING,
            )
            assert hidden == hiding


class TestHeadroomExcluded:
    """Headroom above icons must NOT be in the input region.

    This prevents tooltip oscillation: cursor above icons -> leave ->
    hide -> trigger -> show -> tooltip -> leave -> repeat.
    """

    def test_bottom_headroom_above_icons_excluded(self):
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.VISIBLE,
        )
        # The region starts at (WIN_H - CONTENT_CROSS), so y=0 to that
        # point is outside the input region (headroom).
        assert y > 0
        assert y == WIN_H - CONTENT_CROSS

    def test_top_headroom_below_icons_excluded(self):
        x, y, w, h = compute_input_rect(
            Position.TOP,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            CONTENT_CROSS,
            HideState.VISIBLE,
        )
        assert y == 0
        assert h == CONTENT_CROSS
        assert h < WIN_H  # doesn't cover full height
