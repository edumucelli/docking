"""Tests for auto-hide state machine and easing functions."""

import sys
import pytest
from unittest.mock import MagicMock, MagicMock as MM

# Mock gi before importing autohide so tests run without PyGObject
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.autohide import (  # noqa: E402
    AutoHideController,
    HideState,
    ease_in_cubic,
    ease_out_cubic,
)


class TestEasing:
    def test_ease_in_cubic_at_0(self):
        assert ease_in_cubic(0.0) == pytest.approx(0.0)

    def test_ease_in_cubic_at_1(self):
        assert ease_in_cubic(1.0) == pytest.approx(1.0)

    def test_ease_in_cubic_at_half(self):
        assert ease_in_cubic(0.5) == pytest.approx(0.125)

    def test_ease_out_cubic_at_0(self):
        assert ease_out_cubic(0.0) == pytest.approx(0.0)

    def test_ease_out_cubic_at_1(self):
        assert ease_out_cubic(1.0) == pytest.approx(1.0)

    def test_ease_out_cubic_at_half(self):
        assert ease_out_cubic(0.5) == pytest.approx(0.875)

    def test_ease_in_starts_slow(self):
        """Ease-in should have smaller values at the start."""
        assert ease_in_cubic(0.1) < 0.1

    def test_ease_out_starts_fast(self):
        """Ease-out should have larger values at the start."""
        assert ease_out_cubic(0.1) > 0.1


class TestAutoHideState:
    def _make_controller(self, autohide=True, hide_delay=0, unhide_delay=0):
        window = MagicMock()
        config = MagicMock()
        config.autohide = autohide
        config.hide_delay_ms = hide_delay
        config.unhide_delay_ms = unhide_delay
        config.hide_time_ms = 250
        return AutoHideController(window, config)

    def test_initial_state_is_visible(self):
        ctrl = self._make_controller()
        assert ctrl.state == HideState.VISIBLE
        assert ctrl.hide_offset == 0.0

    def test_not_enabled_does_nothing(self):
        ctrl = self._make_controller(autohide=False)
        ctrl.on_mouse_leave()
        assert ctrl.state == HideState.VISIBLE

    def test_enabled_property(self):
        ctrl = self._make_controller(autohide=True)
        assert ctrl.enabled is True

        ctrl2 = self._make_controller(autohide=False)
        assert ctrl2.enabled is False
