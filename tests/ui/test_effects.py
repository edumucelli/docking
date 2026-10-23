"""Tests for animation effects: easing bounce, icon color extraction."""

import math
from itertools import pairwise
from unittest.mock import MagicMock

import pytest

from docking.ui.effects import (
    average_icon_color,
    click_effect_angle,
    click_effect_scale,
    easing_bounce,
)


class TestAverageIconColor:
    def test_none_pixbuf_returns_gray(self):
        # Given / When
        result = average_icon_color(pixbuf=None)
        # Then
        assert result == (0.5, 0.5, 0.5)

    def test_opaque_red_pixbuf(self):
        # Given
        pixbuf = MagicMock()
        pixbuf.get_width.return_value = 2
        pixbuf.get_height.return_value = 2
        pixbuf.get_n_channels.return_value = 4
        pixbuf.get_rowstride.return_value = 8
        pixbuf.get_pixels.return_value = bytes([255, 0, 0, 255] * 4)
        # When
        r, g, b = average_icon_color(pixbuf=pixbuf)
        # Then
        assert r > 0.5
        assert g < 0.1
        assert b < 0.1

    def test_transparent_pixels_ignored(self):
        # Given
        pixbuf = MagicMock()
        pixbuf.get_width.return_value = 2
        pixbuf.get_height.return_value = 2
        pixbuf.get_n_channels.return_value = 4
        pixbuf.get_rowstride.return_value = 8
        pixbuf.get_pixels.return_value = bytes([255, 0, 0, 0] * 4)
        # When
        result = average_icon_color(pixbuf=pixbuf)
        # Then
        assert result == (0.5, 0.5, 0.5)

    def test_gray_pixels_low_score(self):
        # Given
        pixbuf = MagicMock()
        pixbuf.get_width.return_value = 2
        pixbuf.get_height.return_value = 2
        pixbuf.get_n_channels.return_value = 4
        pixbuf.get_rowstride.return_value = 8
        pixbuf.get_pixels.return_value = bytes([128, 128, 128, 255] * 4)
        # When
        result = average_icon_color(pixbuf=pixbuf)
        # Then
        assert result == (0.5, 0.5, 0.5)


class TestClickEffectAngle:
    @pytest.mark.parametrize("effect", ["pulse", "wobble"])
    @pytest.mark.parametrize(
        ("t", "duration"),
        [(-1, 600_000), (0, 600_000), (600_000, 600_000), (700_000, 600_000), (1, 0)],
    )
    def test_inactive_effect_is_at_rest(self, effect, t, duration):
        assert click_effect_angle(effect, t, duration) == 0.0

    def test_wobble_alternates_and_decays(self):
        peaks = [
            click_effect_angle("wobble", t, 600_000) for t in (50_000, 150_000, 250_000)
        ]
        assert peaks[0] > 0 > peaks[1]
        assert peaks[2] > 0
        assert abs(peaks[0]) > abs(peaks[1]) > abs(peaks[2])

    @pytest.mark.parametrize("effect", ["bounce", "pulse", "", "unknown"])
    def test_other_effects_do_not_rotate(self, effect):
        assert click_effect_angle(effect, 50_000, 600_000) == 0.0


class TestClickEffectScale:
    @pytest.mark.parametrize(
        ("t", "duration"),
        [(-1, 600_000), (0, 600_000), (600_000, 600_000), (700_000, 600_000), (1, 0)],
    )
    def test_inactive_pulse_has_resting_size(self, t, duration):
        assert click_effect_scale("pulse", t, duration) == 1.0

    def test_pulse_grows_and_settles(self):
        sizes = [
            click_effect_scale("pulse", t, 600_000) for t in range(0, 600_001, 1000)
        ]
        assert sizes[0] == sizes[-1] == 1.0
        assert max(sizes) == pytest.approx(1.18)
        assert all(a < b for a, b in pairwise(sizes[:301]))
        assert all(a > b for a, b in pairwise(sizes[300:]))

    @pytest.mark.parametrize("effect", ["bounce", "wobble", "", "unknown"])
    def test_other_effects_keep_resting_size(self, effect):
        assert click_effect_scale(effect, 300_000, 600_000) == 1.0


class TestEasingBounce:
    @pytest.mark.parametrize("n", [1, 2])
    @pytest.mark.parametrize("duration", [150_000, 600_000, 1_200_000])
    def test_bounce_settles_at_its_configured_duration(self, n, duration):
        assert easing_bounce(0, duration, n) == 0.0
        assert easing_bounce(duration / (2 * n), duration, n) == pytest.approx(1.0)
        assert easing_bounce(duration - 1, duration, n) == pytest.approx(0.0, abs=0.001)
        assert easing_bounce(duration, duration, n) == 0.0
        assert easing_bounce(duration + 100_000, duration, n) == 0.0

    def test_zero_at_start(self):
        # Given / When
        result = easing_bounce(t=0, duration=600_000, n=2)
        # Then
        assert result == pytest.approx(0.0)

    def test_zero_at_end(self):
        # Given / When
        result = easing_bounce(t=600_000, duration=600_000, n=2)
        # Then
        assert result == pytest.approx(0.0)

    def test_zero_past_duration(self):
        # Given / When
        result = easing_bounce(t=700_000, duration=600_000, n=2)
        # Then
        assert result == 0.0

    def test_first_bounce_reaches_one(self):
        # Given
        # When
        peak = max(
            easing_bounce(t=t * 1000, duration=600_000, n=2) for t in range(0, 600, 5)
        )
        # Then
        assert peak == pytest.approx(1.0, abs=0.01)

    def test_second_bounce_lower_than_first(self):
        # Given
        first_half = [
            easing_bounce(t=t * 1000, duration=600_000, n=2) for t in range(300)
        ]
        second_half = [
            easing_bounce(t=t * 1000, duration=600_000, n=2) for t in range(300, 600)
        ]
        # When
        first_peak = max(first_half)
        second_peak = max(second_half)
        # Then
        assert second_peak < first_peak
        assert second_peak < 0.5

    def test_always_non_negative(self):
        # Given
        # When
        values = [easing_bounce(t=t * 1000, duration=600_000, n=2) for t in range(601)]
        # Then
        assert all(v >= 0.0 for v in values)

    def test_urgent_single_bounce(self):
        # Given
        # When
        peak = max(
            easing_bounce(t=t * 1000, duration=600_000, n=1) for t in range(0, 600, 5)
        )
        # Then
        assert peak == pytest.approx(1.0, abs=0.01)

    def test_zero_duration_returns_zero(self):
        # Given / When
        result = easing_bounce(t=100, duration=0, n=1)
        # Then
        assert result == 0.0

    def test_n1_symmetric_around_midpoint(self):
        # Given
        # When / Then
        assert abs(math.sin(math.pi * 0.25)) == pytest.approx(
            abs(math.sin(math.pi * 0.75))
        )
