"""Tests for animation effects: easing bounce, icon color extraction."""

import math
from unittest.mock import MagicMock

import pytest

from docking.ui.effects import (
    average_icon_color,
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


class TestEasingBounce:
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
            easing_bounce(t=t * 1000, duration=600_000, n=2) for t in range(0, 300)
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
        values = [
            easing_bounce(t=t * 1000, duration=600_000, n=2) for t in range(0, 601)
        ]
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
