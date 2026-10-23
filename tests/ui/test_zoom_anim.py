"""Tests for the zoom animation in effects.py."""

from __future__ import annotations

from itertools import pairwise
from unittest.mock import MagicMock

import pytest

from docking.ui import effects
from docking.ui.effects import ZoomAnimator, ease_out_cubic

# -- ease_out_cubic -----------------------------------------------------------


class TestEaseOutCubic:
    def test_zero(self):
        assert ease_out_cubic(0.0) == 0.0

    def test_one(self):
        assert ease_out_cubic(1.0) == 1.0

    def test_midpoint_above_linear(self):
        # ease-out should be ahead of linear at midpoint
        assert ease_out_cubic(0.5) > 0.5

    def test_clamps_below_zero(self):
        assert ease_out_cubic(-0.5) == 0.0

    def test_clamps_above_one(self):
        assert ease_out_cubic(1.5) == 1.0

    def test_monotonic(self):
        values = [ease_out_cubic(t / 10) for t in range(11)]
        for a, b in pairwise(values):
            assert b >= a


# -- ZoomAnimator -------------------------------------------------------------


def _make_animator(**kwargs) -> ZoomAnimator:
    da = MagicMock()
    return ZoomAnimator(da, **kwargs)


@pytest.fixture(autouse=True)
def _isolate_animation_timers(monkeypatch):
    monkeypatch.setattr(effects.GLib, "timeout_add", MagicMock(return_value=1234))


class TestZoomAnimatorEnter:
    def test_initial_progress_is_zero(self):
        anim = _make_animator()
        assert anim.progress == 0.0

    def test_on_enter_ramps_up(self, monotonic_clock):
        anim = _make_animator(enter_ms=80)
        anim.on_enter()
        # Simulate enough ticks to finish
        for _ in range(20):
            monotonic_clock.now_us += 16_000
            if not anim._tick():
                break
        assert anim.progress == 1.0

    def test_progress_increases_each_tick(self, monotonic_clock):
        anim = _make_animator(enter_ms=160)
        anim.on_enter()
        prev = anim.progress
        monotonic_clock.now_us += 16_000
        anim._tick()
        assert anim.progress > prev


class TestZoomAnimatorLeave:
    def test_on_leave_ramps_down(self, monotonic_clock):
        anim = _make_animator(leave_ms=80)
        anim._raw = 1.0
        anim.on_leave()
        for _ in range(20):
            monotonic_clock.now_us += 16_000
            if not anim._tick():
                break
        assert anim.progress == 0.0

    def test_progress_decreases_each_tick(self, monotonic_clock):
        anim = _make_animator(leave_ms=160)
        anim._raw = 1.0
        anim.on_leave()
        prev = anim.progress
        monotonic_clock.now_us += 16_000
        anim._tick()
        assert anim.progress < prev


class TestZoomAnimatorReversal:
    def test_enter_then_leave_mid_transition(self, monotonic_clock):
        anim = _make_animator(enter_ms=80, leave_ms=80)
        anim.on_enter()
        monotonic_clock.now_us += 16_000
        anim._tick()
        monotonic_clock.now_us += 16_000
        anim._tick()
        mid = anim._raw
        assert 0.0 < mid < 1.0
        # Reverse direction
        anim.on_leave()
        monotonic_clock.now_us += 16_000
        anim._tick()
        assert anim._raw < mid


class TestZoomAnimatorTimer:
    def test_tick_returns_false_when_done(self, monotonic_clock):
        anim = _make_animator(enter_ms=16)
        anim.on_enter()
        # One tick at 16ms/16ms = full step
        monotonic_clock.now_us += 16_000
        result = anim._tick()
        assert result is False
        assert anim._raw == 1.0

    def test_tick_returns_true_while_animating(self, monotonic_clock):
        anim = _make_animator(enter_ms=160)
        anim.on_enter()
        assert anim._tick() is True

    def test_queue_draw_called_on_tick(self, monotonic_clock):
        da = MagicMock()
        anim = ZoomAnimator(da, enter_ms=160)
        anim.on_enter()
        anim._tick()
        da.queue_draw.assert_called()


class TestZoomElapsedTime:
    @pytest.mark.parametrize("entering", [False, True])
    def test_delayed_first_tick_reaches_target(self, monotonic_clock, entering):
        anim = _make_animator()
        if entering:
            anim.on_enter()
        else:
            anim._raw = 1.0
            anim.on_leave()

        monotonic_clock.now_us += 300_000
        assert anim._tick() is False
        assert anim.progress == (1.0 if entering else 0.0)
        assert anim._timer_id == 0

    def test_idle_time_and_repeated_enter_do_not_change_timing(self, monotonic_clock):
        anim = _make_animator(enter_ms=120)
        monotonic_clock.now_us = 30_000_000
        anim.on_enter()
        for _ in range(3):
            assert anim._tick() is True
        assert anim.progress == 0.0

        monotonic_clock.now_us += 60_000
        anim.on_enter()
        anim._tick()
        assert anim.progress == pytest.approx(0.875)
        monotonic_clock.now_us += 60_000
        assert anim._tick() is False
        assert anim.progress == 1.0

    def test_reversal_uses_time_since_direction_changed(self, monotonic_clock):
        anim = _make_animator(enter_ms=100, leave_ms=200)
        anim.on_enter()
        monotonic_clock.now_us += 50_000
        anim._tick()
        assert anim._raw == pytest.approx(0.5)

        monotonic_clock.now_us += 30_000
        anim.on_leave()
        anim._tick()
        assert anim._raw == pytest.approx(0.5)
        monotonic_clock.now_us += 50_000
        anim._tick()
        assert anim._raw == pytest.approx(0.25)
        monotonic_clock.now_us += 50_000
        assert anim._tick() is False
        assert anim.progress == 0.0

    def test_new_transition_after_idle_gets_its_own_duration(self, monotonic_clock):
        anim = _make_animator(enter_ms=100, leave_ms=200)
        anim.on_enter()
        monotonic_clock.now_us += 100_000
        assert anim._tick() is False

        monotonic_clock.now_us += 30_000_000
        anim.on_leave()
        assert anim._tick() is True
        assert anim.progress == 1.0
        monotonic_clock.now_us += 100_000
        anim._tick()
        assert anim.progress == pytest.approx(0.875)

    @pytest.mark.parametrize("entering", [False, True])
    def test_zero_duration_reaches_target(self, monotonic_clock, entering):
        anim = _make_animator(enter_ms=0, leave_ms=0)
        if entering:
            anim.on_enter()
        else:
            anim._raw = 1.0
            anim.on_leave()
        assert anim._tick() is False
        assert anim.progress == (1.0 if entering else 0.0)
