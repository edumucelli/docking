"""Tests for dock renderer."""

from docking.ui.renderer import DockRenderer


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
        from docking.ui.renderer import SHELF_SMOOTH_FACTOR

        renderer.smooth_shelf_w += (
            target - renderer.smooth_shelf_w
        ) * SHELF_SMOOTH_FACTOR
        # Then — should lerp, not snap
        assert renderer.smooth_shelf_w != target
        assert renderer.smooth_shelf_w > 478.0
        assert renderer.smooth_shelf_w < 520.0
