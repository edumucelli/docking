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

import docking.platform.backends.x11.services.previews as x11_preview_mod
import docking.ui.preview as preview_mod
from docking.platform.backends.base import DisplayServer, WindowId
from docking.platform.backends.x11.impl import preview_capture
from docking.ui.preview import (
    ICON_FALLBACK_SIZE,
    POPUP_PADDING,
    PREVIEW_HIDE_DELAY_MS,
    THUMB_H,
    THUMB_SPACING,
    THUMB_W,
)


class TestX11PreviewService:
    def test_capture_uses_xid_without_live_window_state(self, monkeypatch):
        # Given
        tracker = MagicMock()
        tracker.window_for_id.side_effect = AssertionError(
            "service should not consult Wnck minimized state for popup capture"
        )
        pixbuf = MagicMock()
        pixbuf.get_width.return_value = 120
        pixbuf.get_height.return_value = 80
        monkeypatch.setattr(
            x11_preview_mod,
            "capture_xid",
            MagicMock(return_value=pixbuf),
        )
        service = x11_preview_mod.X11PreviewService(window_tracker=tracker)

        # When
        result = service.capture(WindowId.x11(42), width=200, height=150)

        # Then
        assert result is not None
        assert result.image is pixbuf
        assert result.width == 120
        assert result.height == 80
        x11_preview_mod.capture_xid.assert_called_once_with(
            xid=42, thumb_w=200, thumb_h=150
        )
        tracker.window_for_id.assert_not_called()

    def test_capture_returns_none_when_xid_capture_fails(self, monkeypatch):
        # Given
        tracker = MagicMock()
        monkeypatch.setattr(
            x11_preview_mod, "capture_xid", MagicMock(return_value=None)
        )
        service = x11_preview_mod.X11PreviewService(window_tracker=tracker)

        # When
        result = service.capture(WindowId.x11(42), width=200, height=150)

        # Then
        assert result is None

    def test_thumbnail_uses_live_window_and_backend_fallback(self, monkeypatch):
        tracker = MagicMock()
        window = MagicMock()
        tracker.window_for_id.return_value = window
        pixbuf = MagicMock()
        pixbuf.get_width.return_value = 28
        pixbuf.get_height.return_value = 20
        monkeypatch.setattr(
            x11_preview_mod,
            "capture_window",
            MagicMock(return_value=pixbuf),
        )
        service = x11_preview_mod.X11PreviewService(window_tracker=tracker)

        result = service.thumbnail(WindowId.x11(42), width=28, height=20)

        assert result is not None
        assert result.image is pixbuf
        assert result.width == 28
        assert result.height == 20
        tracker.window_for_id.assert_called_once_with(WindowId.x11(42))
        x11_preview_mod.capture_window.assert_called_once_with(
            wnck_window=window,
            thumb_w=28,
            thumb_h=20,
        )

    def test_thumbnail_rejects_non_x11_window_id(self):
        tracker = MagicMock()
        service = x11_preview_mod.X11PreviewService(window_tracker=tracker)

        result = service.thumbnail(
            WindowId(DisplayServer.WAYLAND, "win-1"), width=28, height=20
        )

        assert result is None
        tracker.window_for_id.assert_not_called()


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
        monkeypatch.setattr(
            preview_capture.GdkPixbuf, "Pixbuf", pixbuf_cls, raising=False
        )
        monkeypatch.setattr(
            preview_capture.Gtk.IconTheme,
            "get_default",
            lambda: None,
            raising=False,
        )
        # When
        result = preview_capture._icon_fallback(thumb_w=120, thumb_h=80)
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
        monkeypatch.setattr(
            preview_capture.GdkPixbuf, "Pixbuf", pixbuf_cls, raising=False
        )

        theme = MagicMock()
        theme.load_icon.return_value = icon
        monkeypatch.setattr(
            preview_capture.Gtk.IconTheme,
            "get_default",
            lambda: theme,
            raising=False,
        )
        # When
        result = preview_capture._icon_fallback(thumb_w=200, thumb_h=150)
        # Then
        assert result is bg
        scaled_icon.composite.assert_called_once()


class TestCaptureWindow:
    def test_returns_icon_fallback_for_minimized_window(self, monkeypatch):
        # Given
        window = MagicMock()
        window.is_minimized.return_value = True
        fallback = MagicMock()
        monkeypatch.setattr(preview_capture, "_icon_fallback", lambda **_k: fallback)
        # When
        result = preview_capture.capture_window(window)
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

        display = MagicMock()
        display.error_trap_pop.return_value = 0
        monkeypatch.setattr(
            preview_capture.GdkX11.X11Display,
            "get_default",
            lambda: display,
            raising=False,
        )
        monkeypatch.setattr(
            preview_capture.GdkX11.X11Window,
            "foreign_new_for_display",
            lambda _display, _xid: foreign,
            raising=False,
        )
        monkeypatch.setattr(
            preview_capture.Gdk,
            "pixbuf_get_from_window",
            lambda *_a, **_k: pixbuf,
            raising=False,
        )
        # When
        result = preview_capture.capture_window(window, thumb_w=200, thumb_h=150)
        # Then
        assert result is scaled
        pixbuf.scale_simple.assert_called_once()

    def test_falls_back_when_foreign_window_lookup_fails(self, monkeypatch):
        # Given
        window = MagicMock()
        window.is_minimized.return_value = False
        window.get_xid.return_value = 100

        fallback = MagicMock()
        monkeypatch.setattr(preview_capture, "_icon_fallback", lambda **_k: fallback)
        monkeypatch.setattr(preview_capture.GLib, "Error", RuntimeError, raising=False)
        monkeypatch.setattr(
            preview_capture.GdkX11.X11Display,
            "get_default",
            lambda: MagicMock(),
            raising=False,
        )
        monkeypatch.setattr(
            preview_capture.GdkX11.X11Window,
            "foreign_new_for_display",
            MagicMock(side_effect=TypeError("bad foreign window")),
            raising=False,
        )
        # When
        result = preview_capture.capture_window(window, thumb_w=180, thumb_h=120)
        # Then
        assert result is fallback

    def test_capture_xid_returns_none_for_black_unavailable_frame(self, monkeypatch):
        # Given
        foreign = MagicMock()
        foreign.get_width.return_value = 4
        foreign.get_height.return_value = 4

        # Fully black opaque pixels.
        pixbuf = MagicMock()
        pixbuf.get_width.return_value = 4
        pixbuf.get_height.return_value = 4
        pixbuf.get_n_channels.return_value = 4
        pixbuf.get_rowstride.return_value = 16
        pixbuf.get_has_alpha.return_value = True
        pixbuf.get_pixels.return_value = bytes([0, 0, 0, 255] * 16)

        display = MagicMock()
        display.error_trap_pop.return_value = 0
        monkeypatch.setattr(
            preview_capture.GdkX11.X11Display,
            "get_default",
            lambda: display,
            raising=False,
        )
        monkeypatch.setattr(
            preview_capture.GdkX11.X11Window,
            "foreign_new_for_display",
            lambda _display, _xid: foreign,
            raising=False,
        )
        monkeypatch.setattr(
            preview_capture.Gdk,
            "pixbuf_get_from_window",
            lambda *_a, **_k: pixbuf,
            raising=False,
        )
        # When
        result = preview_capture.capture_xid(42, thumb_w=200, thumb_h=150)
        # Then
        assert result is None
