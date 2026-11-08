"""Fallback artwork updates without replacing cards or overwriting real captures."""

import os
import subprocess
import sys
from unittest.mock import MagicMock

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.platform.backends.base import PreviewImage
from tests.ui.preview_support import PreviewHarness


def _image(color, width=64, height=64):
    image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
    image.fill(color)
    return image


def test_late_artwork_updates_waiting_card_then_yields_to_real_capture_and_unsubscribes():
    # Isolate native event dispatch from the suite's GTK test doubles, as in
    # test_stack_scroll_events and the native Search layout tests. Reuse the
    # connected display rather than environment changed by earlier tests.
    display = Gdk.Display.get_default()
    assert display is not None
    backend = display.__gtype__.name.removeprefix("Gdk").removesuffix("Display").lower()
    environment = {**os.environ, "GDK_BACKEND": backend}
    if backend == "x11":
        environment["DISPLAY"] = display.get_name()
    elif backend == "wayland":
        environment["WAYLAND_DISPLAY"] = display.get_name()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from tests.ui.test_preview_artwork import _check_late_artwork; "
                "_check_late_artwork()"
            ),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _check_late_artwork():
    harness = PreviewHarness()
    popup = harness.popup
    popup._preview_service.capture.return_value = None
    popup._preview_service.asynchronous = True
    icons = MagicMock()
    first, late = _image(0xCC4422FF), _image(0x2288CCFF)
    icons.load.return_value = first
    popup.set_application_icons(icons)
    try:
        harness.show(1)
        window_id, (widget, _w, _h, _previous) = next(
            iter(popup._thumbnail_images.items())
        )
        assert widget.get_pixbuf() is first
        position = popup.get_position()
        callback = icons.subscribe.call_args.args[0]
        icons.load.return_value = late
        callback("test.desktop")
        assert popup._thumbnail_images[window_id][0] is widget
        assert widget.get_pixbuf() is late
        assert popup.get_position() == position
        real = _image(0x44CC22FF, 200, 150)
        popup._preview_service.capture.return_value = PreviewImage(real, 200, 150)
        popup._refresh_preview_images()
        callback(None)
        assert widget.get_pixbuf() is real
        popup.hide()
        icons.subscribe.return_value.assert_called_once_with()
        callback("test.desktop")
        assert widget.get_pixbuf() is real
    finally:
        harness.close()
