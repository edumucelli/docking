"""Tests for poof animation constants, loader, and draw loop."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import cairo

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.poof as poof_mod  # noqa: E402
from docking.ui.poof import POOF_DURATION_MS  # noqa: E402


class TestPoofConstants:
    def test_duration_reasonable(self):
        # Short enough to feel snappy, long enough to be visible
        assert 100 <= POOF_DURATION_MS <= 500

    def test_poof_svg_exists(self):
        # Given
        svg = (
            Path(__file__).resolve().parent.parent.parent
            / "docking"
            / "assets"
            / "poof.svg"
        )
        # When / Then
        assert svg.exists()


class _FakePixbuf:
    def __init__(self, width: int, height: int):
        self._width = width
        self._height = height

    def get_width(self):
        return self._width

    def get_height(self):
        return self._height


class _FakeScreen:
    def get_rgba_visual(self):
        return object()


class _FakeWindow:
    def __init__(self, type=None):
        self.type = type
        self.signals = {}
        self.destroyed = False
        self.queue_draw_count = 0
        self.x = 0
        self.y = 0

    def set_decorated(self, _value):
        return

    def set_skip_taskbar_hint(self, _value):
        return

    def set_app_paintable(self, _value):
        return

    def set_size_request(self, _w, _h):
        return

    def get_screen(self):
        return _FakeScreen()

    def set_visual(self, _visual):
        return

    def connect(self, signal, callback):
        self.signals[signal] = callback

    def move(self, x, y):
        self.x = x
        self.y = y

    def show_all(self):
        return

    def destroy(self):
        self.destroyed = True

    def queue_draw(self):
        self.queue_draw_count += 1


class TestLoadPoof:
    def test_returns_none_when_asset_load_fails(self, monkeypatch):
        # Given
        poof_mod._load_poof.cache_clear()
        fake_error = type("FakeError", (Exception,), {})
        monkeypatch.setattr(poof_mod.GLib, "Error", fake_error, raising=False)

        pixbuf_cls = MagicMock()
        pixbuf_cls.new_from_file.side_effect = FileNotFoundError
        monkeypatch.setattr(poof_mod.GdkPixbuf, "Pixbuf", pixbuf_cls, raising=False)
        # When
        loaded = poof_mod._load_poof()
        # Then
        assert loaded is None


class TestShowPoof:
    def test_returns_early_when_sprite_is_missing(self, monkeypatch):
        # Given
        monkeypatch.setattr(poof_mod, "_load_poof", lambda: None)
        window_cls = MagicMock()
        monkeypatch.setattr(poof_mod.Gtk, "Window", window_cls, raising=False)
        # When
        poof_mod.show_poof(100, 100)
        # Then
        window_cls.assert_not_called()

    def test_returns_early_when_sprite_has_zero_frames(self, monkeypatch):
        # Given
        monkeypatch.setattr(poof_mod, "_load_poof", lambda: _FakePixbuf(64, 63))
        window_cls = MagicMock()
        monkeypatch.setattr(poof_mod.Gtk, "Window", window_cls, raising=False)
        # When
        poof_mod.show_poof(120, 80)
        # Then
        window_cls.assert_not_called()

    def test_animates_frames_and_destroys_window(self, monkeypatch):
        # Given
        created = []

        def _window_factory(type=None):
            win = _FakeWindow(type=type)
            created.append(win)
            return win

        monkeypatch.setattr(poof_mod, "_load_poof", lambda: _FakePixbuf(64, 192))
        monkeypatch.setattr(
            poof_mod.Gtk,
            "WindowType",
            type("WindowType", (), {"POPUP": "popup"}),
            raising=False,
        )
        monkeypatch.setattr(poof_mod.Gtk, "Window", _window_factory, raising=False)
        monkeypatch.setattr(
            poof_mod.Gdk,
            "cairo_set_source_pixbuf",
            MagicMock(),
            raising=False,
        )

        def _run_animation(_interval, callback, win):
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)
            cr = cairo.Context(surface)
            win.signals["draw"](win, cr)
            while callback(win):
                pass
            return 1

        monkeypatch.setattr(poof_mod.GLib, "timeout_add", _run_animation, raising=False)
        # When
        poof_mod.show_poof(200, 140)
        # Then
        assert len(created) == 1
        assert created[0].destroyed is True
        assert created[0].queue_draw_count == 2
