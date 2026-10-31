"""Desktop geometry, cancellation, source ownership and native resource scopes."""

import ctypes
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from docking.applets.screenshot import windows as mod
from docking.applets.screenshot.state import ScreenshotImage
from docking.platform.backends.base import Rect


def desktop_api(bounds=(-1920, -100, 3200, 1080)):
    api = mod.WindowsScreenshotAPI.__new__(mod.WindowsScreenshotAPI)
    api._metric = lambda index: bounds[index - 76]
    api._flush = Mock(return_value=0)
    return api


def test_desktop_capture_uses_physical_negative_bounds_and_owned_rgb(monkeypatch):
    image = Image.new("RGB", (3200, 1080), (20, 40, 80))
    grab = Mock(return_value=image)
    monkeypatch.setattr(mod.ImageGrab, "grab", grab)
    result = desktop_api().desktop()
    grab.assert_called_once_with(
        bbox=None, all_screens=True, include_layered_windows=True
    )
    assert result.width == 3200 and result.height == 1080
    assert result.pixels[:3] == bytes((20, 40, 80))
    with pytest.raises(ValueError):
        image.getpixel((0, 0))  # The native image was closed after copying.


def test_region_is_physical_and_excludes_pixels_outside_selected_bounds(monkeypatch):
    grab = Mock(return_value=Image.new("RGB", (200, 100), (1, 2, 3)))
    monkeypatch.setattr(mod.ImageGrab, "grab", grab)
    result = desktop_api().desktop(Rect(-1800, 20, 200, 100))
    assert (result.width, result.height) == (200, 100)
    assert grab.call_args.kwargs["bbox"] == (-1800, 20, -1600, 120)


@pytest.mark.parametrize(
    "region", [Rect(-2000, 0, 100, 100), Rect(0, 0, 0, 50), Rect(1000, 0, 500, 100)]
)
def test_outside_or_empty_region_never_invokes_capture(monkeypatch, region):
    grab = Mock()
    monkeypatch.setattr(mod.ImageGrab, "grab", grab)
    with pytest.raises(OSError):
        desktop_api().desktop(region)
    grab.assert_not_called()


def test_changed_display_geometry_discards_result_and_closes_image(monkeypatch):
    image = Image.new("RGB", (100, 100))
    monkeypatch.setattr(mod.ImageGrab, "grab", lambda **_kw: image)
    with pytest.raises(OSError, match="geometry changed"):
        desktop_api().desktop()
    with pytest.raises(ValueError):
        image.getpixel((0, 0))


def test_failed_composition_and_oversize_desktop_do_not_allocate_pixels(monkeypatch):
    grab = Mock()
    monkeypatch.setattr(mod.ImageGrab, "grab", grab)
    api = desktop_api()
    api._flush.return_value = -1
    with pytest.raises(OSError, match="composition"):
        api.desktop()
    with pytest.raises(OSError, match="pixel budget"):
        desktop_api((0, 0, 32768, 32768)).desktop()
    grab.assert_not_called()


def test_backend_rejects_stopped_or_missing_selection_without_native_libraries(
    monkeypatch,
):
    dll = Mock(side_effect=OSError("Unavailable"))
    monkeypatch.setattr(ctypes, "WinDLL", dll, raising=False)
    backend = mod.WindowsScreenshotBackend()
    assert not backend.capture("region").accepted
    backend.stop()
    assert not backend.capture("full").accepted
    assert not backend.available()
    dll.assert_not_called()


def test_stop_during_native_capture_discards_completed_pixels():
    api = Mock()
    backend = mod.WindowsScreenshotBackend(api=api)
    image = ScreenshotImage(bytes((1, 2, 3)), 1, 1)
    api.desktop.side_effect = lambda _region: backend.stop() or image
    assert not backend.capture("full").accepted


def test_window_stream_and_device_close_in_order_on_capture_error(monkeypatch):
    from docking.platform.backends.windows import capture, windows
    from docking.platform.backends.windows.windows import WindowsWindowTarget

    events = []
    native, device, stream = Mock(), Mock(), Mock()
    native.foreground_capture_snapshot.return_value = SimpleNamespace(
        id=SimpleNamespace(value=0x100000001)
    )
    native.target.return_value = WindowsWindowTarget(0x100000001, 55)
    device.close.side_effect = lambda: events.append("device")
    stream.close.side_effect = lambda: events.append("stream")
    stream.read.side_effect = OSError("Window disappeared")
    monkeypatch.setattr(windows, "Win32WindowAPI", lambda **_kw: native)
    monkeypatch.setattr(capture, "WindowsCaptureDevice", lambda: device)
    factory = Mock(return_value=stream)
    monkeypatch.setattr(capture, "WindowsCaptureStream", factory)
    with pytest.raises(OSError):
        desktop_api().window(threading.Event())
    assert events == ["stream", "device"]
    validate = factory.call_args.kwargs["validate"]
    assert validate()
    native.target.return_value = WindowsWindowTarget(0x100000002, 99)
    assert not validate()


def test_scaled_region_preserves_odd_device_pixels_and_negative_monitor_origin(
    monkeypatch,
):
    from gi.repository import Gdk

    from docking.applets.screenshot.state import SelectedRegion
    from docking.platform.backends.windows.geometry import ScreenMapping

    logical = Rect(-640, 0, 640, 400)
    monitor = SimpleNamespace(
        get_geometry=lambda: SimpleNamespace(x=-640, y=0, width=640, height=400),
        get_scale_factor=lambda: 2,
    )
    display = SimpleNamespace(
        get_monitor_at_point=lambda _x, _y: monitor,
        get_n_monitors=lambda: 1,
        get_monitor=lambda _index: monitor,
    )
    geometry = SimpleNamespace(
        mapping=lambda _snapshot, _display: ScreenMapping(
            logical, Rect(-1280, -100, 1280, 800), Rect(-1280, -100, 1280, 800), 2
        )
    )
    monkeypatch.setattr(Gdk.Display, "get_default", lambda: display)
    monkeypatch.setattr(mod, "WindowsMonitorGeometry", lambda: geometry)
    backend = mod.WindowsScreenshotBackend()
    assert backend.prepare_region(SelectedRegion(-614.5, 30.5, 40, 30)) == Rect(
        -1229, -39, 80, 60
    )
    assert backend.prepare_region(SelectedRegion(-10, 10, 20, 20)) is None
