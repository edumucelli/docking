"""Tests for urgent glow opacity calculation."""

import pytest

from docking.ui.renderer import compute_urgent_glow_opacity


class TestUrgentGlowOpacity:
    """compute_urgent_glow_opacity returns pulsing opacity 0.2-0.95."""

    def test_zero_elapsed_is_midpoint(self):
        # Given t=0 -> sin(0) = 0 -> 0.2 + 0.75 * 0.5 = 0.575
        opacity = compute_urgent_glow_opacity(0, 10000, 2000)
        assert opacity == pytest.approx(0.575)

    def test_quarter_pulse_is_peak(self):
        # Given t = pulse/4 -> sin(pi/2) = 1 -> 0.2 + 0.75 = 0.95
        quarter_us = 500 * 1000  # 500ms = quarter of 2000ms pulse
        opacity = compute_urgent_glow_opacity(quarter_us, 10000, 2000)
        assert opacity == pytest.approx(0.95)

    def test_three_quarter_pulse_is_trough(self):
        # Given t = 3*pulse/4 -> sin(3*pi/2) = -1 -> 0.2 + 0.0 = 0.2
        three_quarter_us = 1500 * 1000
        opacity = compute_urgent_glow_opacity(three_quarter_us, 10000, 2000)
        assert opacity == pytest.approx(0.2)

    def test_expired_returns_zero(self):
        # Given elapsed > glow_time
        opacity = compute_urgent_glow_opacity(11_000_000, 10000, 2000)
        assert opacity == 0.0

    def test_negative_elapsed_returns_zero(self):
        assert compute_urgent_glow_opacity(-1, 10000, 2000) == 0.0

    def test_at_exact_glow_time_returns_zero(self):
        # Exactly at the boundary
        opacity = compute_urgent_glow_opacity(10_000_000, 10000, 2000)
        assert opacity == 0.0

    def test_oscillates_over_time(self):
        # Verify it actually oscillates (not constant)
        values = [
            compute_urgent_glow_opacity(t * 100_000, 10000, 2000) for t in range(20)
        ]
        assert max(values) > 0.9
        assert min(values) < 0.3
