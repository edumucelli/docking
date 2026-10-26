"""Screen sampling coordinates, native resource ownership and service lifetime."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gi.repository import Gdk

from docking.platform.backends.base import Rect
from docking.platform.backends.windows import geometry, screen_capture


def native_api(monkeypatch):
    user, gdi, dwm = Mock(), Mock(), Mock()
    user.GetDC.return_value = 0x123456789
    user.ReleaseDC.return_value = 1
    dwm.DwmFlush.return_value = 0
    libraries = {"user32": user, "gdi32": gdi, "dwmapi": dwm}
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name, **kw: libraries[name], raising=False
    )
    monkeypatch.setattr(Gdk.Display, "get_default", lambda: "display")
    api = screen_capture.WindowsScreenAPI()
    api._geometry = Mock()
    api._geometry.point.return_value = (-100, 200)
    return api, user, gdi, dwm


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, (0, 0, 0)),
        (0xFFFFFF, (255, 255, 255)),
        (0x563412, (18, 52, 86)),
        (0xFFFFFFFF, None),
    ],
)
def test_colorref_order_valid_black_white_and_invalid_pixels_release_dc(
    monkeypatch, value, expected
):
    api, user, gdi, _ = native_api(monkeypatch)
    gdi.GetPixel.return_value = value
    assert api.sample(7, 9) == expected
    api._geometry.point.assert_called_once_with(7, 9, "display")
    gdi.GetPixel.assert_called_once_with(0x123456789, -100, 200)
    user.ReleaseDC.assert_called_once_with(None, 0x123456789)
    assert gdi.GetPixel.restype is ctypes.c_uint32
    assert user.GetDC.restype is ctypes.c_void_p


def test_pixel_exception_still_releases_the_owned_dc(monkeypatch):
    api, user, gdi, _ = native_api(monkeypatch)
    gdi.GetPixel.side_effect = OSError("read failed")
    with pytest.raises(OSError):
        api.sample(0, 0)
    user.ReleaseDC.assert_called_once_with(None, 0x123456789)


@pytest.mark.parametrize("unavailable", ["display", "point", "composition", "dc"])
def test_unknown_display_point_composition_or_dc_does_not_read_or_release(
    monkeypatch, unavailable
):
    api, user, gdi, dwm = native_api(monkeypatch)
    if unavailable == "display":
        monkeypatch.setattr(Gdk.Display, "get_default", lambda: None)
    elif unavailable == "point":
        api._geometry.point.return_value = None
    elif unavailable == "composition":
        dwm.DwmFlush.return_value = -1
    else:
        user.GetDC.return_value = None
    assert api.sample(0, 0) is None
    gdi.GetPixel.assert_not_called()
    user.ReleaseDC.assert_not_called()


def test_cannot_publish_a_color_if_dc_release_failed(monkeypatch):
    api, user, gdi, _ = native_api(monkeypatch)
    user.ReleaseDC.return_value = 0
    gdi.GetPixel.return_value = 0
    with pytest.raises(OSError, match="release"):
        api.sample(0, 0)


def test_lazy_creation_and_stopped_service_do_not_load_native_libraries(monkeypatch):
    dll = Mock(side_effect=OSError("unavailable"))
    monkeypatch.setattr(ctypes, "WinDLL", dll, raising=False)
    service = screen_capture.WindowsScreenCaptureService()
    service.start()
    service.stop()
    assert service.pick_color(x=0, y=0) is None
    dll.assert_not_called()
    service.start()
    assert service.pick_color(x=0, y=0) is None
    dll.assert_called_once()


@pytest.mark.parametrize("restart", [False, True])
def test_results_from_an_earlier_lifetime_are_rejected(restart):
    api = Mock()
    service = screen_capture.WindowsScreenCaptureService(api=api)

    def read(x, y):
        service.stop()
        if restart:
            service.start()
        return 1, 2, 3

    api.sample.side_effect = read
    assert service.pick_color(x=10, y=20) is None
    api.sample.side_effect = None
    api.sample.return_value = (4, 5, 6)
    service.start()
    assert service.pick_color(x=10, y=20) == (4, 5, 6)


def test_screen_point_maps_relative_to_exact_negative_origin_and_scale():
    bounds = SimpleNamespace(x=0, y=100, width=1280, height=720)
    monitor = Mock()
    monitor.get_geometry.return_value = bounds
    monitor.get_scale_factor.return_value = 2
    display = Mock()
    display.get_monitor_at_point.return_value = monitor
    display.get_n_monitors.return_value = 1
    display.get_monitor.return_value = monitor
    coordinates = geometry.WindowsMonitorGeometry.__new__(
        geometry.WindowsMonitorGeometry
    )
    coordinates.mapping = Mock(
        return_value=geometry.ScreenMapping(
            Rect(0, 100, 1280, 720),
            Rect(-2560, -1440, 2560, 1440),
            Rect(-2560, -1440, 2560, 1400),
            2,
        )
    )
    assert coordinates.point(123, 145, display) == (-2314, -1350)
    snapshot = coordinates.mapping.call_args.args[0]
    assert snapshot.index == 0 and snapshot.scale == 2
    assert coordinates.point(-1, 145, display) is None
    assert coordinates.point(1280, 145, display) is None
    assert coordinates.mapping.call_count == 1
