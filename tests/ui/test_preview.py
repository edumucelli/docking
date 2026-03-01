"""Tests for preview popup constants and helper functions."""

import sys
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.preview as preview_mod  # noqa: E402
from docking.ui.preview import (  # noqa: E402
    ICON_FALLBACK_SIZE,
    POPUP_PADDING,
    PREVIEW_HIDE_DELAY_MS,
    THUMB_H,
    THUMB_SPACING,
    THUMB_W,
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


class TestPreviewCss:
    def test_ensure_css_installs_only_once(self, monkeypatch):
        # Given
        preview_mod._ensure_css.cache_clear()
        install_mock = MagicMock()
        monkeypatch.setattr(preview_mod, "_install_css", install_mock)
        # When
        preview_mod._ensure_css()
        preview_mod._ensure_css()
        # Then
        install_mock.assert_called_once()


class TestIconFallback:
    def test_returns_background_when_icon_theme_missing(self, monkeypatch):
        # Given
        bg = MagicMock()
        pixbuf_cls = MagicMock()
        pixbuf_cls.new.return_value = bg
        monkeypatch.setattr(preview_mod.GdkPixbuf, "Pixbuf", pixbuf_cls, raising=False)
        monkeypatch.setattr(
            preview_mod.Gtk.IconTheme,
            "get_default",
            lambda: None,
            raising=False,
        )
        # When
        result = preview_mod._icon_fallback(thumb_w=120, thumb_h=80)
        # Then
        assert result is bg
        bg.fill.assert_called_once()

    def test_composites_icon_when_available(self, monkeypatch):
        # Given
        bg = MagicMock()
        scaled_icon = MagicMock()
        icon = MagicMock()
        icon.scale_simple.return_value = scaled_icon

        pixbuf_cls = MagicMock()
        pixbuf_cls.new.return_value = bg
        monkeypatch.setattr(preview_mod.GdkPixbuf, "Pixbuf", pixbuf_cls, raising=False)

        theme = MagicMock()
        theme.load_icon.return_value = icon
        monkeypatch.setattr(
            preview_mod.Gtk.IconTheme,
            "get_default",
            lambda: theme,
            raising=False,
        )
        # When
        result = preview_mod._icon_fallback(thumb_w=200, thumb_h=150)
        # Then
        assert result is bg
        scaled_icon.composite.assert_called_once()


class TestCaptureWindow:
    def test_returns_icon_fallback_for_minimized_window(self, monkeypatch):
        # Given
        window = MagicMock()
        window.is_minimized.return_value = True
        fallback = MagicMock()
        monkeypatch.setattr(preview_mod, "_icon_fallback", lambda **_k: fallback)
        # When
        result = preview_mod.capture_window(window)
        # Then
        assert result is fallback

    def test_captures_and_scales_foreign_window(self, monkeypatch):
        # Given
        window = MagicMock()
        window.is_minimized.return_value = False
        window.get_xid.return_value = 42

        foreign = MagicMock()
        foreign.get_width.return_value = 400
        foreign.get_height.return_value = 200

        pixbuf = MagicMock()
        scaled = MagicMock()
        pixbuf.scale_simple.return_value = scaled

        monkeypatch.setattr(
            preview_mod.GdkX11.X11Display,
            "get_default",
            lambda: MagicMock(),
            raising=False,
        )
        monkeypatch.setattr(
            preview_mod.GdkX11.X11Window,
            "foreign_new_for_display",
            lambda _display, _xid: foreign,
            raising=False,
        )
        monkeypatch.setattr(
            preview_mod.Gdk,
            "pixbuf_get_from_window",
            lambda *_a, **_k: pixbuf,
            raising=False,
        )
        # When
        result = preview_mod.capture_window(window, thumb_w=200, thumb_h=150)
        # Then
        assert result is scaled
        pixbuf.scale_simple.assert_called_once()

    def test_falls_back_when_foreign_window_lookup_fails(self, monkeypatch):
        # Given
        window = MagicMock()
        window.is_minimized.return_value = False
        window.get_xid.return_value = 100

        fallback = MagicMock()
        monkeypatch.setattr(preview_mod, "_icon_fallback", lambda **_k: fallback)
        monkeypatch.setattr(preview_mod.GLib, "Error", RuntimeError, raising=False)
        monkeypatch.setattr(
            preview_mod.GdkX11.X11Display,
            "get_default",
            lambda: MagicMock(),
            raising=False,
        )
        monkeypatch.setattr(
            preview_mod.GdkX11.X11Window,
            "foreign_new_for_display",
            MagicMock(side_effect=TypeError("bad foreign window")),
            raising=False,
        )
        # When
        result = preview_mod.capture_window(window, thumb_w=180, thumb_h=120)
        # Then
        assert result is fallback
