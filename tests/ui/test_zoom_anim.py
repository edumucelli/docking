"""Tests for the zoom animation in effects.py."""

from __future__ import annotations

from itertools import pairwise
from unittest.mock import MagicMock

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


class TestZoomAnimatorEnter:
    def test_initial_progress_is_zero(self):
        anim = _make_animator()
        assert anim.progress == 0.0

    def test_on_enter_ramps_up(self):
        anim = _make_animator(enter_ms=80)
        anim.on_enter()
        # Simulate enough ticks to finish
        for _ in range(20):
            if not anim._tick():
                break
        assert anim.progress == 1.0

    def test_progress_increases_each_tick(self):
        anim = _make_animator(enter_ms=160)
        anim.on_enter()
        prev = anim.progress
        anim._tick()
        assert anim.progress > prev


class TestZoomAnimatorLeave:
    def test_on_leave_ramps_down(self):
        anim = _make_animator(leave_ms=80)
        anim._raw = 1.0
        anim.on_leave()
        for _ in range(20):
            if not anim._tick():
                break
        assert anim.progress == 0.0

    def test_progress_decreases_each_tick(self):
        anim = _make_animator(leave_ms=160)
        anim._raw = 1.0
        anim.on_leave()
        prev = anim.progress
        anim._tick()
        assert anim.progress < prev


class TestZoomAnimatorReversal:
    def test_enter_then_leave_mid_transition(self):
        anim = _make_animator(enter_ms=80, leave_ms=80)
        anim.on_enter()
        anim._tick()
        anim._tick()
        mid = anim._raw
        assert 0.0 < mid < 1.0
        # Reverse direction
        anim.on_leave()
        anim._tick()
        assert anim._raw < mid


class TestZoomAnimatorTimer:
    def test_tick_returns_false_when_done(self):
        anim = _make_animator(enter_ms=16)
        anim.on_enter()
        # One tick at 16ms/16ms = full step
        result = anim._tick()
        assert result is False
        assert anim._raw == 1.0

    def test_tick_returns_true_while_animating(self):
        anim = _make_animator(enter_ms=160)
        anim.on_enter()
        assert anim._tick() is True

    def test_queue_draw_called_on_tick(self):
        da = MagicMock()
        anim = ZoomAnimator(da, enter_ms=160)
        anim.on_enter()
        anim._tick()
        da.queue_draw.assert_called()
