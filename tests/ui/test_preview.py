"""Tests for preview popup constants."""

import sys
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.ui.preview import (  # noqa: E402
    THUMB_W,
    THUMB_H,
    POPUP_PADDING,
    THUMB_SPACING,
    PREVIEW_HIDE_DELAY_MS,
    ICON_FALLBACK_SIZE,
)


class TestPreviewConstants:
    def test_thumbnail_dimensions_positive(self):
        assert THUMB_W > 0
        assert THUMB_H > 0

    def test_thumbnail_landscape(self):
        # Thumbnails should be wider than tall (landscape)
        assert THUMB_W > THUMB_H

    def test_padding_positive(self):
        assert POPUP_PADDING > 0
        assert THUMB_SPACING > 0

    def test_hide_delay_reasonable(self):
        # Enough time to move mouse to popup, not so long it feels stuck
        assert 100 <= PREVIEW_HIDE_DELAY_MS <= 1000

    def test_icon_fallback_size(self):
        assert ICON_FALLBACK_SIZE > 0
        assert ICON_FALLBACK_SIZE <= min(THUMB_W, THUMB_H)
