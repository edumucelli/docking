"""Capture pixels, stale HWNDs and nonblocking Windows preview publication."""

import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.backends.base import DisplayServer, WindowId, WindowSnapshot
from docking.platform.backends.windows.capture import CapturePixels, rgba_pixels
from docking.platform.backends.windows.previews import WindowsPreviewService, _image


@pytest.mark.parametrize("padding", [0, 1, 3, 16])
def test_capture_channels_alpha_stride_and_owned_pixel_storage(padding):
    row = bytes((0, 0, 255, 255, 64, 32, 16, 128, 90, 80, 70, 0))
    source = (row + b"\x99" * padding) * 2
    result = rgba_pixels(source, 3, 2, len(row) + padding)
    assert result.pixels == bytes((255, 0, 0, 255, 31, 63, 127, 128, 0, 0, 0, 0)) * 2
    assert source == (row + b"\x99" * padding) * 2
    image = _image(result, 2, 2)
    assert (image.width, image.height) == (2, 1)
    assert len(image.image.get_pixels()) >= 2 * 4


@pytest.mark.parametrize(
    ("width", "height", "stride"),
    [(0, 1, 4), (1, 0, 4), (1, 1, 3), (16385, 1, 65540), (1, 1, 300000), (1, 1, 4)],
)
def test_invalid_or_truncated_gpu_buffer_is_rejected(width, height, stride):
    with pytest.raises(ValueError):
        rgba_pixels(b"", width, height, stride)


def _service():
    row = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 100),
        desktop_id="fixture",
        pid=101,
        visible=True,
    )
    windows = SimpleNamespace(list_all_windows=MagicMock(return_value=(row,)))
    service = WindowsPreviewService(windows=windows, availability_changed=MagicMock())
    service.start = MagicMock()  # The pure cache tests must not start a native worker.
    return service, windows, row


def test_capture_queues_once_returns_immediately_and_later_uses_a_fitted_image():
    service, windows, row = _service()
    for _ in range(10):
        assert service.capture(row.id, width=6, height=6) is None
    assert service._queue.qsize() == 1
    key = service._queue.get_nowait()
    service._publish(key, CapturePixels(bytes((255, 0, 0, 255)) * 32, 8, 4))
    image = service.capture(row.id, width=6, height=6)
    assert image is not None and (image.width, image.height) == (6, 3)
    assert service.thumbnail(row.id, width=6, height=6) is image
    windows.list_all_windows.return_value = ()
    assert service.capture(row.id, width=6, height=6) is None
    assert not service._cache
    service.stop()


def test_reused_hwnd_never_inherits_another_process_image_or_late_result():
    service, windows, row = _service()
    service.capture(row.id, width=6, height=6)
    old = service._queue.get_nowait()
    pixels = CapturePixels(bytes((255, 0, 0, 255)) * 32, 8, 4)
    service._publish(old, pixels)
    windows.list_all_windows.return_value = (replace(row, pid=202),)
    assert service.capture(row.id, width=6, height=6) is None
    service._publish(old, pixels)
    assert not service._cache
    assert service.capture(row.id, width=6, height=6) is None
    assert service._queue.qsize() == 1
    service.stop()


def test_unavailable_capture_does_not_keep_queuing_work():
    service, _windows, row = _service()
    service._mark_unavailable()
    for _ in range(100):
        assert service.capture(row.id, width=6, height=6) is None
    assert service._queue.empty()
    service._availability_changed.assert_called_once_with(False)
    service.stop()


def test_native_worker_owns_and_expires_its_stream_without_waiting_on_gtk(monkeypatch):
    from docking.platform.backends.windows import previews as module

    entered, expired = threading.Event(), threading.Event()
    events = []
    main_thread = threading.get_ident()

    class Device:
        def __init__(self):
            events.append(("device", threading.get_ident()))

        def close(self):
            events.append(("device-close", threading.get_ident()))

    class Stream:
        def __init__(self, *_args):
            events.append(("stream", threading.get_ident()))

        def read(self):
            entered.set()
            return CapturePixels(bytes((255, 0, 0, 255)) * 32, 8, 4)

        def close(self):
            events.append(("stream-close", threading.get_ident()))
            expired.set()

    monkeypatch.setattr(module, "WindowsCaptureDevice", Device)
    monkeypatch.setattr(module, "WindowsCaptureStream", Stream)
    monkeypatch.setattr(module, "_STREAM_IDLE_SECONDS", 0.02)
    row = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 100),
        desktop_id="fixture",
        pid=101,
        visible=True,
    )
    windows = SimpleNamespace(list_all_windows=lambda: (row,))
    service = WindowsPreviewService(windows=windows, availability_changed=MagicMock())
    monkeypatch.setattr(service, "_post", lambda *_args: None)
    try:
        service.capture(row.id, width=6, height=6)
        assert entered.wait(2)
        assert expired.wait(2)
    finally:
        service.stop()
        service._thread.join(timeout=2)
    assert [name for name, _thread in events] == [
        "device",
        "stream",
        "stream-close",
        "device-close",
    ]
    assert len({thread for _name, thread in events}) == 1
    assert events[0][1] != main_thread


def test_resize_frame_with_old_gpu_capacity_is_retried_without_mapping(monkeypatch):
    import ctypes

    from docking.platform.backends.windows import capture as module

    calls = []

    def native_method(_pointer, index, _result, *_arguments):
        calls.append(index)
        assert index == 10

        def description(_texture, output):
            output._obj.width = 642
            output._obj.height = 392
            output._obj.format = 87

        return description

    monkeypatch.setattr(module, "method", native_method)
    stream = object.__new__(module.WindowsCaptureStream)
    stream._resize_pending = False
    assert stream._read_texture(ctypes.c_void_p(1), module._Size(806, 473)) is None
    assert stream._resize_pending
    assert calls == [10]
