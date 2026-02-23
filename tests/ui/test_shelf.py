"""Tests for shelf background drawing constants."""

from docking.ui.shelf import SHELF_HEIGHT_PX


class TestShelfConstants:
    def test_shelf_height_reasonable(self):
        # Given / When / Then — shelf should be shorter than a typical icon
        assert 10 <= SHELF_HEIGHT_PX <= 30
