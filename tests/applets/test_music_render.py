"""Tests for music icon rendering helpers."""

from __future__ import annotations

import cairo
import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

import docking.applets.music.render as music_render_mod


def _pixbuf(w: int = 32, h: int = 32) -> GdkPixbuf.Pixbuf:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, w, h)
    assert pixbuf is not None
    pixbuf.fill(0x3366CCFF)
    return pixbuf


class TestMusicRenderInternals:
    def test_crop_center_square_success_and_edge_cases(self, monkeypatch):
        cropped = music_render_mod._crop_center_square(_pixbuf(64, 32), 24)
        assert cropped is not None
        assert cropped.get_width() == 24
        assert cropped.get_height() == 24

        class _BrokenPixbuf:
            def get_width(self):
                return 0

            def get_height(self):
                return 10

        assert music_render_mod._crop_center_square(_BrokenPixbuf(), 24) is None

        class _NoScalePixbuf:
            def get_width(self):
                return 10

            def get_height(self):
                return 10

            def scale_simple(self, *_args, **_kwargs):
                return None

        assert music_render_mod._crop_center_square(_NoScalePixbuf(), 12) is None

        source = _pixbuf(10, 10)
        monkeypatch.setattr(
            music_render_mod.GdkPixbuf.Pixbuf, "new", lambda *a, **k: None
        )
        assert music_render_mod._crop_center_square(source, 8) is None

    def test_draw_album_art_branches(self, monkeypatch):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)
        cr = cairo.Context(surface)

        monkeypatch.setattr(
            music_render_mod, "_crop_center_square", lambda pixbuf, size: None
        )
        music_render_mod._draw_album_art(cr, 64, _pixbuf(32, 32))

        monkeypatch.setattr(
            music_render_mod,
            "_crop_center_square",
            lambda pixbuf, size: _pixbuf(size, size),
        )
        music_render_mod._draw_album_art(cr, 64, _pixbuf(32, 32))

    def test_idle_tile_pixbuf_cache_and_fallbacks(self, monkeypatch):
        music_render_mod._NO_PLAYER_ICON_CACHE.clear()
        p1 = music_render_mod._idle_music_tile_pixbuf(48)
        p2 = music_render_mod._idle_music_tile_pixbuf(48)
        assert p1 is not None
        assert p2 is p1

        monkeypatch.setattr(
            music_render_mod.Gdk, "pixbuf_get_from_surface", lambda *a, **k: None
        )
        music_render_mod._NO_PLAYER_ICON_CACHE.clear()
        assert music_render_mod._idle_music_tile_pixbuf(48) is None

        class _NoScale:
            def scale_simple(self, *_args, **_kwargs):
                return None

        monkeypatch.setattr(
            music_render_mod.Gdk, "pixbuf_get_from_surface", lambda *a, **k: _NoScale()
        )
        music_render_mod._NO_PLAYER_ICON_CACHE.clear()
        pixbuf = music_render_mod._idle_music_tile_pixbuf(48)
        assert pixbuf is not None

    def test_draw_idle_tile_falls_back_to_vector_when_cached_pixbuf_missing(
        self, monkeypatch
    ):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)
        cr = cairo.Context(surface)
        monkeypatch.setattr(
            music_render_mod, "_idle_music_tile_pixbuf", lambda size: None
        )
        music_render_mod._draw_idle_music_tile(cr, 64)

    def test_idle_tile_has_no_control_bar_and_centers_note(self):
        size = 100
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        music_render_mod._draw_idle_music_tile_vector(cr, size)
        pixbuf = music_render_mod.Gdk.pixbuf_get_from_surface(
            surface,
            0,
            0,
            size,
            size,
        )
        assert pixbuf is not None
        pixels = pixbuf.get_pixels()
        rowstride = pixbuf.get_rowstride()
        channels = pixbuf.get_n_channels()

        def rgb_at(x: int, y: int) -> tuple[int, int, int]:
            offset = y * rowstride + x * channels
            return tuple(pixels[offset : offset + 3])

        assert rgb_at(10, 95) == (253, 249, 165)
        assert rgb_at(90, 95) == (251, 242, 74)

        dark_pixels = [
            (x, y)
            for y in range(size)
            for x in range(size)
            if all(channel < 100 for channel in rgb_at(x, y))
        ]
        min_x = min(x for x, _ in dark_pixels)
        max_x = max(x for x, _ in dark_pixels)
        min_y = min(y for _, y in dark_pixels)
        max_y = max(y for _, y in dark_pixels)
        assert abs((min_x + max_x) / 2 - size / 2) <= 3
        assert abs((min_y + max_y) / 2 - size / 2) <= 3

    def test_volume_steps_and_badges(self):
        assert music_render_mod._volume_step_count(0) == 0
        assert music_render_mod._volume_step_count(10) == 1
        assert music_render_mod._volume_step_count(40) == 2
        assert music_render_mod._volume_step_count(70) == 3
        assert music_render_mod._volume_step_count(99) == 4

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 80, 80)
        cr = cairo.Context(surface)
        music_render_mod._draw_volume_badge(cr, 80, 0)
        music_render_mod._draw_volume_badge(cr, 80, 80)
        music_render_mod._draw_status_badge(cr, 80, "Paused")
        music_render_mod._draw_status_badge(cr, 80, "Playing")
        music_render_mod._draw_status_badge(cr, 80, "Stopped")

    def test_create_music_icon_branches(self):
        assert (
            music_render_mod.create_music_icon(
                size=48,
                playback_status="Playing",
                album_art=None,
                volume_percent=50,
                available=False,
            )
            is not None
        )
        assert (
            music_render_mod.create_music_icon(
                size=48,
                playback_status="Playing",
                album_art=_pixbuf(24, 24),
                volume_percent=50,
                available=True,
            )
            is not None
        )
        assert (
            music_render_mod.create_music_icon(
                size=48,
                playback_status="Paused",
                album_art=None,
                volume_percent=0,
                available=True,
            )
            is not None
        )

    def test_missing_album_art_uses_default_yellow_tile(self, monkeypatch):
        default_tile_sizes: list[int] = []
        monkeypatch.setattr(
            music_render_mod,
            "_draw_idle_music_tile",
            lambda cr, size: default_tile_sizes.append(size),
        )

        pixbuf = music_render_mod.create_music_icon(
            size=48,
            playback_status="Playing",
            album_art=None,
            volume_percent=50,
            available=True,
        )

        assert pixbuf is not None
        assert default_tile_sizes == [48]

    def test_rounded_rect_path_executes(self):
        from docking.applets.draw import rounded_rect

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 20, 20)
        cr = cairo.Context(surface)
        rounded_rect(cr=cr, x=0, y=0, width=10, height=10, radius=100)
