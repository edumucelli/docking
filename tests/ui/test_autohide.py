"""Tests for auto-hide state machine and easing functions."""

import sys
from unittest.mock import MagicMock

import pytest

# Mock gi before importing autohide so tests run without PyGObject
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.autohide as autohide_mod  # noqa: E402
from docking.ui.autohide import (  # noqa: E402
    AutoHideController,
    HideState,
    ease_in_cubic,
    ease_out_cubic,
)


class TestEasing:
    def test_ease_in_cubic_at_0(self):
        # Given / When
        result = ease_in_cubic(t=0.0)
        # Then
        assert result == pytest.approx(0.0)

    def test_ease_in_cubic_at_1(self):
        # Given / When
        result = ease_in_cubic(t=1.0)
        # Then
        assert result == pytest.approx(1.0)

    def test_ease_in_cubic_at_half(self):
        # Given / When
        result = ease_in_cubic(t=0.5)
        # Then
        assert result == pytest.approx(0.125)

    def test_ease_out_cubic_at_0(self):
        # Given / When
        result = ease_out_cubic(t=0.0)
        # Then
        assert result == pytest.approx(0.0)

    def test_ease_out_cubic_at_1(self):
        # Given / When
        result = ease_out_cubic(t=1.0)
        # Then
        assert result == pytest.approx(1.0)

    def test_ease_out_cubic_at_half(self):
        # Given / When
        result = ease_out_cubic(t=0.5)
        # Then
        assert result == pytest.approx(0.875)

    def test_ease_in_starts_slow(self):
        """Ease-in should have smaller values at the start."""
        # Given / When
        result = ease_in_cubic(t=0.1)
        # Then
        assert result < 0.1

    def test_ease_out_starts_fast(self):
        """Ease-out should have larger values at the start."""
        # Given / When
        result = ease_out_cubic(t=0.1)
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
        # Given
        ctrl = self._make_controller()
        ctrl.state = HideState.HIDING
        ctrl.hide_offset = 0.5
        # Simulate one animation tick
        ctrl._anim_progress = 0.5
        ctrl._animation_tick()
        # Then
        assert ctrl.zoom_progress == pytest.approx(1.0 - ctrl.hide_offset, abs=0.1)

    def test_zoom_progress_zero_when_fully_hidden(self):
        ctrl = self._make_controller()
        ctrl.state = HideState.HIDING
        ctrl._anim_progress = 0.99
        ctrl._animation_tick()
        # At full hide, zoom_progress should be near 0
        assert ctrl.zoom_progress <= 0.05

    def test_zoom_progress_ramps_during_showing(self):
        # Given
        ctrl = self._make_controller()
        ctrl.state = HideState.SHOWING
        ctrl._anim_progress = 0.3
        ctrl.hide_offset = 1.0
        ctrl._animation_tick()
        # Then
        assert ctrl.zoom_progress > 0.0

    def test_zoom_progress_1_when_fully_shown(self):
        ctrl = self._make_controller()
        ctrl.state = HideState.SHOWING
        ctrl._anim_progress = 0.99
        ctrl._animation_tick()
        # near fully shown
        assert ctrl.zoom_progress > 0.9


class TestAutoHideUtilityBranches:
    def test_source_exists_handles_all_paths(self, monkeypatch):
        assert autohide_mod._source_exists(0) is False

        ctx = MagicMock()
        ctx.find_source_by_id.return_value = object()
        monkeypatch.setattr(autohide_mod.GLib.MainContext, "default", lambda: ctx)
        assert autohide_mod._source_exists(10) is True

        def boom():
            raise RuntimeError("bad")

        monkeypatch.setattr(autohide_mod.GLib.MainContext, "default", boom)
        assert autohide_mod._source_exists(11) is True

    def test_clear_source_removes_only_when_present(self, monkeypatch):
        removed = []
        monkeypatch.setattr(
            autohide_mod, "_source_exists", lambda source_id: source_id == 7
        )
        monkeypatch.setattr(
            autohide_mod.GLib, "source_remove", lambda sid: removed.append(sid)
        )
        assert autohide_mod._clear_source(7) == 0
        assert autohide_mod._clear_source(8) == 0
        assert removed == [7]


class TestAutoHideTimersAndDelays:
    def _make_controller(self, hide_delay=0, unhide_delay=0):
        window = MagicMock()
        config = MagicMock()
        config.autohide = True
        config.hide_delay_ms = hide_delay
        config.unhide_delay_ms = unhide_delay
        config.hide_time_ms = 250
        return AutoHideController(window, config)

    def test_mouse_leave_with_delay_schedules_hide_timer(self, monkeypatch):
        ctrl = self._make_controller(hide_delay=120)
        monkeypatch.setattr(autohide_mod.GLib, "timeout_add", lambda delay, cb: 901)
        ctrl.on_mouse_leave()
        assert ctrl._hide_timer_id == 901

    def test_mouse_enter_with_delay_schedules_unhide_timer(self, monkeypatch):
        ctrl = self._make_controller(unhide_delay=140)
        ctrl.state = HideState.HIDDEN
        monkeypatch.setattr(autohide_mod.GLib, "timeout_add", lambda delay, cb: 902)
        ctrl.on_mouse_enter()
        assert ctrl._unhide_timer_id == 902

    def test_start_hiding_and_showing_set_state_and_start_animation(self):
        ctrl = self._make_controller()
        ctrl._start_animation = MagicMock()
        assert ctrl._start_hiding() is False
        assert ctrl.state == HideState.HIDING
        assert ctrl._anim_progress == 0.0

        assert ctrl._start_showing() is False
        assert ctrl.state == HideState.SHOWING
        assert ctrl._anim_progress == 0.0
        assert ctrl._start_animation.call_count == 2

    def test_start_animation_replaces_existing_timer(self, monkeypatch):
        ctrl = self._make_controller()
        ctrl._anim_timer_id = 77
        monkeypatch.setattr(autohide_mod, "_clear_source", lambda source_id: 0)
        monkeypatch.setattr(autohide_mod.GLib, "timeout_add", lambda _ms, _cb: 333)
        ctrl._start_animation()
        assert ctrl._anim_timer_id == 333

    def test_cancel_timer_helpers_clear_ids(self, monkeypatch):
        ctrl = self._make_controller()
        monkeypatch.setattr(autohide_mod, "_clear_source", lambda source_id: 0)
        ctrl._hide_timer_id = 11
        ctrl._unhide_timer_id = 12
        ctrl._cancel_hide_timer()
        ctrl._cancel_unhide_timer()
        assert ctrl._hide_timer_id == 0
        assert ctrl._unhide_timer_id == 0
