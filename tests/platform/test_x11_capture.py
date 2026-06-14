"""Tests for X11 screen capture service."""

from __future__ import annotations

from docking.platform.backends.x11.impl import screen_capture
from docking.platform.backends.x11.services import capture
from docking.platform.backends.x11.services.capture import X11ScreenCaptureService


def test_pick_color_delegates_to_x11_pixel_helper(monkeypatch):
    monkeypatch.setattr(capture, "pick_pixel", lambda **_kwargs: (1, 2, 3))

    assert X11ScreenCaptureService().pick_color(x=10, y=20) == (1, 2, 3)


def test_pick_pixel_without_root_window(monkeypatch):
    monkeypatch.setattr(screen_capture.Gdk, "get_default_root_window", lambda: None)
    assert screen_capture.pick_pixel(1, 2) is None


def test_pick_pixel_without_pixbuf(monkeypatch):
    monkeypatch.setattr(screen_capture.Gdk, "get_default_root_window", lambda: object())
    monkeypatch.setattr(
        screen_capture.Gdk,
        "pixbuf_get_from_window",
        lambda root, x, y, w, h: None,
    )
    assert screen_capture.pick_pixel(1, 2) is None


def test_pick_pixel_reads_rgb_triplet(monkeypatch):
    class _PB:
        def get_pixels(self):
            return [12, 34, 56, 200]

    monkeypatch.setattr(screen_capture.Gdk, "get_default_root_window", lambda: object())
    monkeypatch.setattr(
        screen_capture.Gdk,
        "pixbuf_get_from_window",
        lambda root, x, y, w, h: _PB(),
    )

    assert screen_capture.pick_pixel(10, 20) == (12, 34, 56)
