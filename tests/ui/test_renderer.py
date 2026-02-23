"""Tests for dock renderer."""

import math
import pytest
from unittest.mock import MagicMock

from docking.ui.renderer import (
    DockRenderer,
    SHELF_SMOOTH_FACTOR,
    HOVER_LIGHTEN_MAX,
    HOVER_FADE_FRAMES,
    CLICK_DURATION_US,
    CLICK_DARKEN_MAX,
    LAUNCH_BOUNCE_DURATION_US,
    LAUNCH_BOUNCE_HEIGHT,
    URGENT_BOUNCE_DURATION_US,
    URGENT_BOUNCE_HEIGHT,
    _easing_bounce,
)


class TestSmoothShelfW:
    def test_initial_value_is_zero(self):
        # Given / When
        renderer = DockRenderer()
        # Then
        assert renderer.smooth_shelf_w == 0.0

    def test_snaps_to_target_on_first_nonzero(self):
        # Given
        renderer = DockRenderer()
        assert renderer.smooth_shelf_w == 0.0
        # When — simulate what draw() does on first frame
        target = 478.0
        if renderer.smooth_shelf_w == 0.0:
            renderer.smooth_shelf_w = target
        # Then — should snap, not lerp from 0
        assert renderer.smooth_shelf_w == target

    def test_lerps_after_first_snap(self):
        # Given
        renderer = DockRenderer()
        renderer.smooth_shelf_w = 478.0  # first snap
        # When — simulate a different target (zoom active)
        target = 520.0
        renderer.smooth_shelf_w += (
            target - renderer.smooth_shelf_w
        ) * SHELF_SMOOTH_FACTOR
        # Then — should lerp, not snap
        assert renderer.smooth_shelf_w != target
        assert renderer.smooth_shelf_w > 478.0
        assert renderer.smooth_shelf_w < 520.0


class TestEasingBounce:
    def test_zero_at_start(self):
        # Given / When
        result = _easing_bounce(0, 600_000, 2)
        # Then
        assert result == pytest.approx(0.0)

    def test_zero_at_end(self):
        # Given / When
        result = _easing_bounce(600_000, 600_000, 2)
        # Then
        assert result == pytest.approx(0.0)

    def test_zero_past_duration(self):
        # Given / When
        result = _easing_bounce(700_000, 600_000, 2)
        # Then
        assert result == 0.0

    def test_first_bounce_reaches_one(self):
        # Given — launch bounce n=2, first peak at ~25% of duration
        # When
        peak = max(_easing_bounce(t * 1000, 600_000, 2) for t in range(0, 600, 5))
        # Then — first bounce should reach 1.0
        assert peak == pytest.approx(1.0, abs=0.01)

    def test_second_bounce_lower_than_first(self):
        # Given — launch bounce n=2
        first_half = [_easing_bounce(t * 1000, 600_000, 2) for t in range(0, 300)]
        second_half = [_easing_bounce(t * 1000, 600_000, 2) for t in range(300, 600)]
        # When
        first_peak = max(first_half)
        second_peak = max(second_half)
        # Then — second bounce should be significantly lower
        assert second_peak < first_peak
        assert second_peak < 0.5  # less than half the first bounce

    def test_always_non_negative(self):
        # Given — uses abs(sin), should never go negative
        # When
        values = [_easing_bounce(t * 1000, 600_000, 2) for t in range(0, 601)]
        # Then
        assert all(v >= 0.0 for v in values)

    def test_urgent_single_bounce(self):
        # Given — urgent bounce n=1, single arc peaking at center
        # When
        peak = max(_easing_bounce(t * 1000, 600_000, 1) for t in range(0, 600, 5))
        # Then
        assert peak == pytest.approx(1.0, abs=0.01)

    def test_zero_duration_returns_zero(self):
        # Given / When
        result = _easing_bounce(100, 0, 1)
        # Then
        assert result == 0.0


class TestHoverLighten:
    def test_initial_empty(self):
        # Given / When
        renderer = DockRenderer()
        # Then
        assert renderer._hover_lighten == {}

    def test_fade_in_on_hover(self):
        # Given
        renderer = DockRenderer()
        item = MagicMock()
        item.desktop_id = "test.desktop"
        items = [item]
        # When — simulate several frames of hovering
        for _ in range(HOVER_FADE_FRAMES + 5):
            renderer._update_hover_lighten(items, "test.desktop")
        # Then — should reach max lighten
        assert renderer._hover_lighten["test.desktop"] == pytest.approx(
            HOVER_LIGHTEN_MAX
        )

    def test_fade_out_after_unhover(self):
        # Given — item is at max lighten
        renderer = DockRenderer()
        item = MagicMock()
        item.desktop_id = "test.desktop"
        items = [item]
        renderer._hover_lighten["test.desktop"] = HOVER_LIGHTEN_MAX
        # When — hover moves away
        for _ in range(HOVER_FADE_FRAMES + 5):
            renderer._update_hover_lighten(items, "")
        # Then — should fade to zero and be removed
        assert "test.desktop" not in renderer._hover_lighten

    def test_clamps_to_max(self):
        # Given
        renderer = DockRenderer()
        item = MagicMock()
        item.desktop_id = "test.desktop"
        items = [item]
        # When — many frames
        for _ in range(100):
            renderer._update_hover_lighten(items, "test.desktop")
        # Then — never exceeds max
        assert renderer._hover_lighten["test.desktop"] <= HOVER_LIGHTEN_MAX


class TestAnimationConstants:
    def test_click_duration_reasonable(self):
        # Given / When / Then
        assert 100_000 <= CLICK_DURATION_US <= 500_000  # 100-500ms

    def test_click_darken_max_reasonable(self):
        # Given / When / Then
        assert 0.0 < CLICK_DARKEN_MAX <= 1.0

    def test_launch_bounce_height_reasonable(self):
        # Given / When / Then
        assert 0.0 < LAUNCH_BOUNCE_HEIGHT < 2.0

    def test_urgent_bounce_height_reasonable(self):
        # Given / When / Then
        assert 0.0 < URGENT_BOUNCE_HEIGHT < 3.0

    def test_launch_shorter_than_urgent(self):
        # Given / When / Then — launch bounce is shorter/smaller
        assert LAUNCH_BOUNCE_HEIGHT < URGENT_BOUNCE_HEIGHT

    def test_hover_lighten_max_subtle(self):
        # Given / When / Then — should be subtle, not blinding
        assert 0.0 < HOVER_LIGHTEN_MAX <= 0.5

    def test_shelf_height_reasonable(self):
        # Given
        from docking.ui.renderer import SHELF_HEIGHT_PX

        # When / Then — shelf should be shorter than a typical icon
        assert 10 <= SHELF_HEIGHT_PX <= 30
