"""Tests for auto-hide state machine and easing functions."""

import sys
import pytest
from unittest.mock import MagicMock, MagicMock as MM

# Mock gi before importing autohide so tests run without PyGObject
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.ui.autohide import (  # noqa: E402
    AutoHideController,
    HideState,
    ease_in_cubic,
    ease_out_cubic,
)


class TestEasing:
    def test_ease_in_cubic_at_0(self):
        # Given / When
        result = ease_in_cubic(0.0)
        # Then
        assert result == pytest.approx(0.0)

    def test_ease_in_cubic_at_1(self):
        # Given / When
        result = ease_in_cubic(1.0)
        # Then
        assert result == pytest.approx(1.0)

    def test_ease_in_cubic_at_half(self):
        # Given / When
        result = ease_in_cubic(0.5)
        # Then
        assert result == pytest.approx(0.125)

    def test_ease_out_cubic_at_0(self):
        # Given / When
        result = ease_out_cubic(0.0)
        # Then
        assert result == pytest.approx(0.0)

    def test_ease_out_cubic_at_1(self):
        # Given / When
        result = ease_out_cubic(1.0)
        # Then
        assert result == pytest.approx(1.0)

    def test_ease_out_cubic_at_half(self):
        # Given / When
        result = ease_out_cubic(0.5)
        # Then
        assert result == pytest.approx(0.875)

    def test_ease_in_starts_slow(self):
        """Ease-in should have smaller values at the start."""
        # Given / When
        result = ease_in_cubic(0.1)
        # Then
        assert result < 0.1

    def test_ease_out_starts_fast(self):
        """Ease-out should have larger values at the start."""
        # Given / When
        result = ease_out_cubic(0.1)
        # Then
        assert result > 0.1


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
        # Given / When
        ctrl = self._make_controller()
        # Then
        assert ctrl.state == HideState.VISIBLE
        assert ctrl.hide_offset == 0.0

    def test_not_enabled_does_nothing(self):
        # Given
        ctrl = self._make_controller(autohide=False)
        # When
        ctrl.on_mouse_leave()
        # Then
        assert ctrl.state == HideState.VISIBLE

    def test_enabled_property(self):
        # Given / When
        ctrl = self._make_controller(autohide=True)
        ctrl2 = self._make_controller(autohide=False)
        # Then
        assert ctrl.enabled is True
        assert ctrl2.enabled is False

    def test_reset_forces_visible(self):
        # Given
        ctrl = self._make_controller()
        ctrl.state = HideState.HIDDEN
        ctrl.hide_offset = 1.0
        # When
        ctrl.reset()
        # Then
        assert ctrl.state == HideState.VISIBLE
        assert ctrl.hide_offset == 0.0

    def test_reset_from_hiding(self):
        # Given
        ctrl = self._make_controller()
        ctrl.state = HideState.HIDING
        ctrl.hide_offset = 0.5
        # When
        ctrl.reset()
        # Then
        assert ctrl.state == HideState.VISIBLE
        assert ctrl.hide_offset == 0.0

    def test_reset_from_showing(self):
        # Given
        ctrl = self._make_controller()
        ctrl.state = HideState.SHOWING
        ctrl.hide_offset = 0.4
        ctrl._anim_timer_id = 42
        ctrl._unhide_timer_id = 7
        # When
        ctrl.reset()
        # Then
        assert ctrl.state == HideState.VISIBLE
        assert ctrl.hide_offset == 0.0
        assert ctrl._anim_timer_id == 0

    def test_reset_when_already_visible(self):
        # Given
        ctrl = self._make_controller()
        # When
        ctrl.reset()
        # Then
        assert ctrl.state == HideState.VISIBLE
        assert ctrl.hide_offset == 0.0


class TestZoomProgressFormula:
    """zoom_progress must use linear formula (1 - hide_offset), not compound.

    Plank: zoom_in_progress = zoom_progress * (1 - hide_progress).
    A compound formula (zp *= (1 - offset)) decays too aggressively late
    in the animation, causing icons to snap to rest instead of smoothly
    compressing.
    """

    def _make_controller(self):
        window = MagicMock()
        config = MagicMock()
        config.autohide = True
        config.hide_delay_ms = 0
        config.unhide_delay_ms = 0
        config.hide_time_ms = 250
        return AutoHideController(window, config)

    def test_zoom_progress_is_linear_with_hide_offset(self):
        # Given -- simulate mid-hide
        ctrl = self._make_controller()
        ctrl.state = HideState.HIDING
        ctrl.hide_offset = 0.5
        # Simulate one animation tick
        ctrl._anim_progress = 0.5
        ctrl._animation_tick()
        # Then -- zoom_progress should be 1 - hide_offset (linear)
        assert ctrl.zoom_progress == pytest.approx(1.0 - ctrl.hide_offset, abs=0.1)

    def test_zoom_progress_zero_when_fully_hidden(self):
        ctrl = self._make_controller()
        ctrl.state = HideState.HIDING
        ctrl._anim_progress = 0.99
        ctrl._animation_tick()
        # At full hide, zoom_progress should be near 0
        assert ctrl.zoom_progress <= 0.05

    def test_zoom_progress_ramps_during_showing(self):
        # Given -- partway through show animation
        ctrl = self._make_controller()
        ctrl.state = HideState.SHOWING
        ctrl._anim_progress = 0.3
        ctrl.hide_offset = 1.0
        ctrl._animation_tick()
        # Then -- zoom_progress should be positive (dock expanding)
        assert ctrl.zoom_progress > 0.0

    def test_zoom_progress_1_when_fully_shown(self):
        ctrl = self._make_controller()
        ctrl.state = HideState.SHOWING
        ctrl._anim_progress = 0.99
        ctrl._animation_tick()
        # near fully shown
        assert ctrl.zoom_progress > 0.9
