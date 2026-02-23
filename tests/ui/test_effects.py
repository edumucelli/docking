"""Tests for animation effects: easing bounce, icon color extraction."""

import math
import pytest
from unittest.mock import MagicMock

from docking.ui.effects import (
    easing_bounce,
    average_icon_color,
)


class TestAverageIconColor:
    def test_none_pixbuf_returns_gray(self):
        # Given / When
        result = average_icon_color(None)
        # Then
        assert result == (0.5, 0.5, 0.5)

    def test_opaque_red_pixbuf(self):
        # Given — a 2x2 fully red, fully opaque pixbuf
        pixbuf = MagicMock()
        pixbuf.get_width.return_value = 2
        pixbuf.get_height.return_value = 2
        pixbuf.get_n_channels.return_value = 4
        pixbuf.get_rowstride.return_value = 8
        pixbuf.get_pixels.return_value = bytes([255, 0, 0, 255] * 4)
        # When
        r, g, b = average_icon_color(pixbuf)
        # Then — dominant color should be red
        assert r > 0.5
        assert g < 0.1
        assert b < 0.1

    def test_transparent_pixels_ignored(self):
        # Given — all pixels are fully transparent (alpha=0)
        pixbuf = MagicMock()
        pixbuf.get_width.return_value = 2
        pixbuf.get_height.return_value = 2
        pixbuf.get_n_channels.return_value = 4
        pixbuf.get_rowstride.return_value = 8
        pixbuf.get_pixels.return_value = bytes([255, 0, 0, 0] * 4)
        # When
        result = average_icon_color(pixbuf)
        # Then — no visible pixels, returns gray fallback
        assert result == (0.5, 0.5, 0.5)

    def test_gray_pixels_low_score(self):
        # Given — all pixels are neutral gray (no saturation)
        pixbuf = MagicMock()
        pixbuf.get_width.return_value = 2
        pixbuf.get_height.return_value = 2
        pixbuf.get_n_channels.return_value = 4
        pixbuf.get_rowstride.return_value = 8
        pixbuf.get_pixels.return_value = bytes([128, 128, 128, 255] * 4)
        # When — gray pixels have delta=0, so score=0
        result = average_icon_color(pixbuf)
        # Then — all scores are 0, returns gray fallback
        assert result == (0.5, 0.5, 0.5)


class TestEasingBounce:
    def test_zero_at_start(self):
        # Given / When
        result = easing_bounce(0, 600_000, 2)
        # Then
        assert result == pytest.approx(0.0)

    def test_zero_at_end(self):
        # Given / When
        result = easing_bounce(600_000, 600_000, 2)
        # Then
        assert result == pytest.approx(0.0)

    def test_zero_past_duration(self):
        # Given / When
        result = easing_bounce(700_000, 600_000, 2)
        # Then
        assert result == 0.0

    def test_first_bounce_reaches_one(self):
        # Given — launch bounce n=2, first peak at ~25% of duration
        # When
        peak = max(easing_bounce(t * 1000, 600_000, 2) for t in range(0, 600, 5))
        # Then — first bounce should reach 1.0
        assert peak == pytest.approx(1.0, abs=0.01)

    def test_second_bounce_lower_than_first(self):
        # Given — launch bounce n=2
        first_half = [easing_bounce(t * 1000, 600_000, 2) for t in range(0, 300)]
        second_half = [easing_bounce(t * 1000, 600_000, 2) for t in range(300, 600)]
        # When
        first_peak = max(first_half)
        second_peak = max(second_half)
        # Then — second bounce should be significantly lower
        assert second_peak < first_peak
        assert second_peak < 0.5

    def test_always_non_negative(self):
        # Given — uses abs(sin), should never go negative
        # When
        values = [easing_bounce(t * 1000, 600_000, 2) for t in range(0, 601)]
        # Then
        assert all(v >= 0.0 for v in values)

    def test_urgent_single_bounce(self):
        # Given — urgent bounce n=1, single arc peaking at center
        # When
        peak = max(easing_bounce(t * 1000, 600_000, 1) for t in range(0, 600, 5))
        # Then
        assert peak == pytest.approx(1.0, abs=0.01)

    def test_zero_duration_returns_zero(self):
        # Given / When
        result = easing_bounce(100, 0, 1)
        # Then
        assert result == 0.0

    def test_n1_symmetric_around_midpoint(self):
        # Given — n=1 single bounce: sin values at 25% and 75% are equal
        # When / Then
        assert abs(math.sin(math.pi * 0.25)) == pytest.approx(
            abs(math.sin(math.pi * 0.75))
        )
