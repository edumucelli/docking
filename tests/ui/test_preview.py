"""Tests for preview popup constants and helper functions."""

import sys
from unittest.mock import MagicMock

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import pytest

import docking.ui.preview as preview_mod
from docking.ui.preview import (
    ICON_FALLBACK_SIZE,
    LABEL_MIN_CHARS,
    POPUP_PADDING,
    PREVIEW_HIDE_DELAY_MS,
    THUMB_H,
    THUMB_SPACING,
    THUMB_W,
    label_max_chars,
    thumbnail_size,
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
        assert PREVIEW_HIDE_DELAY_MS >= 100
        assert PREVIEW_HIDE_DELAY_MS <= 1000

    def test_icon_fallback_size(self):
        assert ICON_FALLBACK_SIZE > 0
        assert min(THUMB_W, THUMB_H) >= ICON_FALLBACK_SIZE


class TestThumbnailSizing:
    def test_default_width_keeps_todays_geometry(self):
        assert thumbnail_size(200) == (200, 150)
        assert label_max_chars(200) == 25

    @pytest.mark.parametrize(
        ("width", "height"), [(120, 90), (122, 92), (320, 240), (400, 300)]
    )
    def test_height_follows_four_by_three_rounded_half_up(self, width, height):
        assert thumbnail_size(width) == (width, height)

    def test_label_chars_scale_with_width_with_a_floor(self):
        assert label_max_chars(320) == 40
        assert label_max_chars(400) == 50
        assert label_max_chars(40) == LABEL_MIN_CHARS


class TestPreviewCss:
    def test_ensure_css_installs_only_once(self, monkeypatch):
        # Given
        preview_mod._ensure_css.cache_clear()
        install_mock = MagicMock()
        monkeypatch.setattr(preview_mod, "_install_css", install_mock)
        # When
        try:
            preview_mod._ensure_css()
            preview_mod._ensure_css()
            # Then
            install_mock.assert_called_once()
        finally:
            preview_mod._ensure_css.cache_clear()
