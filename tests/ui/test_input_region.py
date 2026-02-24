"""Tests for input region computation — regression guard for tooltip/zoom snap."""

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


class TestVisibleContentOnly:
    """When autohide is VISIBLE or off, input region must be content-only.

    Using full-window region causes tooltip popups to trigger spurious
    crossing events, making icons snap/de-zoom when the tooltip appears.
    """

    def test_autohide_visible_bottom_is_content_only(self):
        # Given — autohide on, state=VISIBLE
        # When
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            HideState.VISIBLE,
        )
        # Then — width matches content, not full window
        assert w == CONTENT_W
        assert x == CONTENT_OFFSET

    def test_autohide_visible_top_is_content_only(self):
        x, y, w, h = compute_input_rect(
            Position.TOP,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            HideState.VISIBLE,
        )
        assert w == CONTENT_W
        assert x == CONTENT_OFFSET

    def test_autohide_visible_left_is_content_only(self):
        x, y, w, h = compute_input_rect(
            Position.LEFT,
            WIN_H,
            WIN_W,
            CONTENT_OFFSET,
            CONTENT_W,
            HideState.VISIBLE,
        )
        assert h == CONTENT_W
        assert y == CONTENT_OFFSET

    def test_autohide_visible_right_is_content_only(self):
        x, y, w, h = compute_input_rect(
            Position.RIGHT,
            WIN_H,
            WIN_W,
            CONTENT_OFFSET,
            CONTENT_W,
            HideState.VISIBLE,
        )
        assert h == CONTENT_W
        assert y == CONTENT_OFFSET

    def test_autohide_off_is_content_only(self):
        # Given — autohide disabled (state=None)
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
            None,
        )
        assert w == CONTENT_W


class TestShowingFullWindow:
    """During SHOWING animation, use full window to prevent oscillation."""

    def test_showing_uses_full_window(self):
        x, y, w, h = compute_input_rect(
            Position.BOTTOM,
            WIN_W,
            WIN_H,
            CONTENT_OFFSET,
            CONTENT_W,
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
                HideState.HIDDEN,
            )
            hiding = compute_input_rect(
                pos,
                WIN_W,
                WIN_H,
                CONTENT_OFFSET,
                CONTENT_W,
                HideState.HIDING,
            )
            assert hidden == hiding
