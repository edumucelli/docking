"""Selection cancellation and atomic screenshot export preserve user data."""

from types import SimpleNamespace
from unittest.mock import Mock

import gi
import pytest

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib
from PIL import Image

from docking.applets.screenshot import selection
from docking.applets.screenshot.linux import UnixScreenshotBackend
from docking.applets.screenshot.preview import save_png
from docking.applets.screenshot.state import ScreenshotImage, SelectedRegion


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ((10, 20), (110, 70), SelectedRegion(10, 20, 100, 50)),
        ((110, 70), (10, 20), SelectedRegion(10, 20, 100, 50)),
        ((-100, -50), (10, 20), SelectedRegion(-100, -50, 110, 70)),
        ((25.5, 30.5), (65.5, 60.5), SelectedRegion(25.5, 30.5, 40, 30)),
    ],
)
def test_drag_uses_root_coordinates_and_handles_any_direction(
    monkeypatch, start, end, expected
):
    monkeypatch.setattr(selection, "create_capture_overlay", lambda **_kw: Mock())
    monkeypatch.setattr(selection, "dismiss_capture_overlay", Mock())
    result = Mock()
    picker = selection.RegionSelection(
        result, anchor=None, backend=UnixScreenshotBackend()
    )
    picker._press(Mock(), SimpleNamespace(button=1, x_root=start[0], y_root=start[1]))
    picker._release(Mock(), SimpleNamespace(button=1, x_root=end[0], y_root=end[1]))
    result.assert_called_once_with(expected)
    assert picker.window is None


def test_right_click_cancels_without_reading_pixels(monkeypatch):
    monkeypatch.setattr(selection, "create_capture_overlay", lambda **_kw: Mock())
    monkeypatch.setattr(selection, "dismiss_capture_overlay", Mock())
    result = Mock()
    picker = selection.RegionSelection(
        result, anchor=None, backend=UnixScreenshotBackend()
    )
    picker._press(Mock(), SimpleNamespace(button=3))
    result.assert_not_called()
    assert picker.window is None


def test_atomic_png_export_contains_exact_captured_pixels(tmp_path):
    pixel = bytes((30, 60, 90))
    data = ScreenshotImage(pixel * 6, 3, 2)
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(data.pixels), GdkPixbuf.Colorspace.RGB, False, 8, 3, 2, 9
    )
    path = tmp_path / "capture ü.png"
    save_png(pixbuf, path)
    with Image.open(path) as exported:
        assert exported.size == (3, 2) and exported.tobytes() == data.pixels
    assert list(tmp_path.iterdir()) == [path]


def test_failed_encoding_does_not_replace_previous_file_or_leave_temp_data(tmp_path):
    path = tmp_path / "capture.png"
    path.write_bytes(b"previous")

    def fail(filename, *_args):
        from pathlib import Path

        Path(filename).write_bytes(b"partial")
        raise OSError("Disk full")

    with pytest.raises(OSError):
        save_png(SimpleNamespace(savev=fail), path)
    assert path.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [path]


def test_preview_fits_the_monitor_and_destroys_its_native_chooser(monkeypatch):
    from gi.repository import Gtk

    from docking.applets.screenshot import preview

    monkeypatch.setattr(preview.ScreenshotPreview, "copy", Mock())
    chooser = Mock()
    monkeypatch.setattr(Gtk.FileChooserNative, "new", Mock(return_value=chooser))
    image = ScreenshotImage(bytes((1, 2, 3)) * (1280 * 800), 1280, 800)
    view = preview.ScreenshotPreview(image, anchor=None)
    try:
        shown = view.dialog.get_content_area().get_children()[0].get_pixbuf()
        monitor = view.dialog.get_display().get_monitor(0).get_workarea()
        assert shown.get_width() <= monitor.width - 80
        assert shown.get_height() <= monitor.height - 140
        view._save_as()
        chooser.set_do_overwrite_confirmation.assert_called_once_with(True)
        chooser.show.assert_called_once()
    finally:
        view.close()
    chooser.destroy.assert_called_once()
    assert view._closed and view._chooser is None


def test_save_uses_the_exact_path_confirmed_by_the_chooser(monkeypatch, tmp_path):
    from gi.repository import Gtk

    from docking.applets.screenshot import preview

    monkeypatch.setattr(preview.ScreenshotPreview, "copy", Mock())
    save = Mock()
    monkeypatch.setattr(preview, "save_png", save)
    selected = tmp_path / "capture"
    sibling = selected.with_suffix(".png")
    sibling.write_bytes(b"Unconfirmed existing file")
    view = preview.ScreenshotPreview(
        ScreenshotImage(bytes((1, 2, 3)), 1, 1), anchor=None
    )
    view._worker = SimpleNamespace(run=lambda **kw: kw["fn"]())
    chooser = Mock()
    chooser.get_filename.return_value = str(selected)
    try:
        view._save_response(chooser, Gtk.ResponseType.ACCEPT)
        save.assert_called_once_with(view.pixbuf, selected)
        assert sibling.read_bytes() == b"Unconfirmed existing file"
    finally:
        view.close()
