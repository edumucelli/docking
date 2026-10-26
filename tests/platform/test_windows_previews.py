"""Capture pixels, stale HWNDs and nonblocking Windows preview publication."""

import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.backends.base import DisplayServer, WindowId, WindowSnapshot
from docking.platform.backends.windows.capture import CapturePixels, rgba_pixels
from docking.platform.backends.windows.previews import WindowsPreviewService
from docking.platform.backends.windows.windows import WindowsWindowTarget


@pytest.mark.parametrize("padding", [0, 1, 3, 16])
def test_capture_channels_alpha_stride_and_owned_pixel_storage(padding):
    row = bytes((0, 0, 255, 255, 64, 32, 16, 128, 90, 80, 70, 0))
    source = (row + b"\x99" * padding) * 2
    result = rgba_pixels(source, 3, 2, len(row) + padding)
    assert result.pixels == bytes((255, 0, 0, 255, 31, 63, 127, 128, 0, 0, 0, 0)) * 2
    assert source == (row + b"\x99" * padding) * 2
    service, _windows, row = _service()
    service.capture(row.id, width=2, height=2)
    service._publish(service._queue.get_nowait(), result)
    image = service.capture(row.id, width=2, height=2)
    assert image is not None and (image.width, image.height) == (2, 1)
    assert len(image.image.get_pixels()) >= 2 * 4
    service.stop()


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
    windows = SimpleNamespace(
        list_all_windows=MagicMock(return_value=(row,)),
        capture_target=MagicMock(
            side_effect=lambda window_id, pid, _app_id: WindowsWindowTarget(
                int(window_id.value), pid
            )
        ),
        capture_is_current=MagicMock(return_value=True),
    )
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


def test_static_frame_serves_new_sizes_without_another_gpu_frame_or_upscaling():
    service, _windows, row = _service()
    colors = (
        (255, 0, 0, 255),
        (0, 255, 0, 255),
        (0, 0, 255, 255),
        (255, 255, 0, 255),
        (255, 0, 255, 255),
        (0, 255, 255, 255),
        (100, 80, 60, 255),
        (5, 10, 15, 255),
    )
    pixels = CapturePixels(b"".join(bytes(color) for color in colors) * 4, 8, 4)
    assert service.capture(row.id, width=2, height=2) is None
    first = service._queue.get_nowait()
    service._publish(first, pixels)
    small = service.capture(row.id, width=2, height=2)
    assert small is not None and (small.width, small.height) == (2, 1)
    full = service.capture(row.id, width=8, height=4)
    assert full is not None and (full.width, full.height) == (8, 4)
    assert bytes(full.image.get_pixels()) == pixels.pixels
    different = service.thumbnail(row.id, width=4, height=4)
    assert different is not None and (different.width, different.height) == (4, 2)
    assert service._queue.empty()
    # A read with no new frame must retain the current captured source.
    service._publish(first, None)
    assert service.capture(row.id, width=8, height=4) is full
    service.stop()


def test_concurrent_size_requests_share_one_pending_native_capture():
    service, _windows, row = _service()
    assert service.capture(row.id, width=8, height=4) is None
    assert service.capture(row.id, width=4, height=4) is None
    assert service.capture(row.id, width=2, height=2) is None
    assert service._queue.qsize() == 1
    service.stop()


def test_a_new_native_frame_replaces_fitted_images_at_every_size():
    service, _windows, row = _service()
    service.capture(row.id, width=2, height=2)
    key = service._queue.get_nowait()
    service._publish(key, CapturePixels(bytes((255, 0, 0, 255)) * 32, 8, 4))
    old = service.capture(row.id, width=8, height=4)
    assert old is not None and bytes(old.image.get_pixels())[:4] == bytes(
        (255, 0, 0, 255)
    )
    service._publish(key, CapturePixels(bytes((0, 0, 255, 255)) * 32, 8, 4))
    for width in (2, 4, 8):
        image = service.capture(row.id, width=width, height=4)
        assert image is not None
        assert bytes(image.image.get_pixels())[:4] == bytes((0, 0, 255, 255))
    assert bytes(old.image.get_pixels())[:4] == bytes((255, 0, 0, 255))
    service.stop()


def test_source_and_fitted_images_share_the_memory_budget(monkeypatch):
    from docking.platform.backends.windows import previews as module

    monkeypatch.setattr(module, "_CACHE_BYTES", 256)
    service, windows, row = _service()
    rows = [
        replace(row, id=WindowId(DisplayServer.WINDOWS, value), pid=value + 1)
        for value in (100, 200, 300)
    ]
    windows.list_all_windows.return_value = rows
    for current in rows:
        service.capture(current.id, width=4, height=4)
        service._publish(
            service._queue.get_nowait(),
            CapturePixels(bytes((255, 0, 0, 255)) * 32, 8, 4),
        )
        retained = sum(
            len(frame.pixbuf.get_pixels()) for frame in service._frames.values()
        )
        retained += sum(
            len(cached.image.image.get_pixels()) for cached in service._cache.values()
        )
        assert retained <= 256
    assert len(service._frames) == 2
    assert all(identity[0] != rows[0].id for identity in service._frames)
    service.stop()
    assert not service._frames and not service._cache


def test_source_cache_retains_only_preview_resolution_and_bounds_entry_count(
    monkeypatch,
):
    from docking.platform.backends.windows import previews as module

    monkeypatch.setattr(module, "_MAX_PREVIEW_SIZE", 8)
    service, windows, row = _service()
    rows = [
        replace(row, id=WindowId(DisplayServer.WINDOWS, value)) for value in range(70)
    ]
    windows.list_all_windows.return_value = rows
    for current in rows:
        service.capture(current.id, width=4, height=4)
        service._publish(
            service._queue.get_nowait(),
            CapturePixels(bytes((255, 0, 0, 255)) * 5000, 100, 50),
        )
    assert len(service._frames) == len(service._cache) == 64
    assert all(
        (frame.pixbuf.get_width(), frame.pixbuf.get_height()) == (8, 4)
        for frame in service._frames.values()
    )
    assert all(key.identity in service._frames for key in service._cache)
    service._mark_unavailable()
    assert not service._frames and not service._cache
    service.stop()


def test_failed_fit_keeps_source_cache_bounded_and_can_retry_without_new_pixels(
    monkeypatch,
):
    from docking.platform.backends.windows import previews as module

    monkeypatch.setattr(module, "_CACHE_BYTES", 256)
    scale = module.GdkPixbuf.Pixbuf.scale_simple
    monkeypatch.setattr(module.GdkPixbuf.Pixbuf, "scale_simple", lambda *_args: None)
    service, windows, row = _service()
    rows = [
        replace(row, id=WindowId(DisplayServer.WINDOWS, value))
        for value in (100, 200, 300)
    ]
    windows.list_all_windows.return_value = rows
    for current in rows:
        service.capture(current.id, width=4, height=4)
        service._publish(
            service._queue.get_nowait(),
            CapturePixels(bytes((255, 0, 0, 255)) * 32, 8, 4),
        )
    assert len(service._frames) == 2 and not service._cache
    monkeypatch.setattr(module.GdkPixbuf.Pixbuf, "scale_simple", scale)
    assert service.capture(rows[-1].id, width=4, height=4) is not None
    service.stop()


def test_reused_hwnd_never_inherits_another_process_image_or_late_result():
    service, windows, row = _service()
    service.capture(row.id, width=6, height=6)
    old = service._queue.get_nowait()
    pixels = CapturePixels(bytes((255, 0, 0, 255)) * 32, 8, 4)
    service._publish(old, pixels)
    windows.list_all_windows.return_value = (replace(row, pid=202),)
    assert service.capture(row.id, width=6, height=6) is None
    assert not service._frames
    service._publish(old, pixels)
    assert not service._cache
    assert service.capture(row.id, width=6, height=6) is None
    assert service._queue.qsize() == 1
    service.stop()


def test_failed_source_resize_preserves_the_last_valid_captured_frame(monkeypatch):
    from docking.platform.backends.windows import previews as module

    monkeypatch.setattr(module, "_MAX_PREVIEW_SIZE", 8)
    service, _windows, row = _service()
    service.capture(row.id, width=8, height=8)
    key = service._queue.get_nowait()
    service._publish(key, CapturePixels(bytes((255, 0, 0, 255)) * 32, 8, 4))
    previous = service.capture(row.id, width=8, height=8)
    assert previous is not None
    monkeypatch.setattr(module.GdkPixbuf.Pixbuf, "scale_simple", lambda *_args: None)
    service._publish(key, CapturePixels(bytes((0, 0, 255, 255)) * 128, 16, 8))
    assert service.capture(row.id, width=8, height=8) is previous
    assert bytes(previous.image.get_pixels())[:4] == bytes((255, 0, 0, 255))
    assert len(service._frames) == 1 and key.identity in service._retry_after
    service.stop()


def test_unavailable_capture_does_not_keep_queuing_work():
    service, _windows, row = _service()
    service._mark_unavailable()
    for _ in range(100):
        assert service.capture(row.id, width=6, height=6) is None
    assert service._queue.empty()
    service._availability_changed.assert_called_once_with(False)
    service.stop()


def test_host_reassigning_same_hwnd_and_pid_cannot_reuse_cached_or_late_app_pixels():
    service, windows, row = _service()
    windows.list_all_windows.return_value = (replace(row, app_id="Old.App"),)
    service.capture(row.id, width=6, height=6)
    old = service._queue.get_nowait()
    pixels = CapturePixels(bytes((255, 0, 0, 255)) * 32, 8, 4)
    service._publish(old, pixels)
    assert service.capture(row.id, width=6, height=6) is not None
    windows.list_all_windows.return_value = (replace(row, app_id="New.App"),)
    assert service.capture(row.id, width=6, height=6) is None
    assert not service._frames
    service._publish(old, pixels)
    assert not service._cache
    new = service._queue.get_nowait()
    assert new != old
    service._publish(new, pixels)
    assert service.capture(row.id, width=6, height=6) is not None
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
        def __init__(self, device, hwnd, pid, *, validate):
            assert (hwnd, pid) == (900, 500)
            assert validate()
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
    windows = SimpleNamespace(
        list_all_windows=lambda: (row,),
        capture_target=lambda *_args: WindowsWindowTarget(900, 500),
        capture_is_current=lambda *_args: True,
    )
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


def test_ghost_transition_and_recovery_discard_old_surfaces_and_late_results():
    service, windows, row = _service()
    pixels = CapturePixels(bytes((255, 0, 0, 255)) * 32, 8, 4)
    service.capture(row.id, width=6, height=6)
    original = service._queue.get_nowait()
    service._publish(original, pixels)
    assert service.capture(row.id, width=6, height=6) is not None
    windows.capture_target.side_effect = None
    windows.capture_target.return_value = WindowsWindowTarget(900, 500)
    assert service.capture(row.id, width=6, height=6) is None
    ghost = service._queue.get_nowait()
    assert ghost.identity != original.identity
    windows.capture_is_current.side_effect = lambda _id, _pid, _app, target: (
        target.hwnd == 900
    )
    service._publish(original, pixels)
    assert not service._cache
    service._publish(ghost, pixels)
    assert service.capture(row.id, width=6, height=6) is not None
    windows.capture_target.return_value = WindowsWindowTarget(100, 101)
    windows.capture_is_current.side_effect = lambda _id, _pid, _app, target: (
        target.hwnd == 100
    )
    assert service.capture(row.id, width=6, height=6) is None
    service._publish(ghost, pixels)
    assert not service._cache
    recovered = service._queue.get_nowait()
    service._publish(recovered, pixels)
    assert service.capture(row.id, width=6, height=6) is not None
    windows.capture_target.return_value = None
    assert service.capture(row.id, width=6, height=6) is None
    assert not service._cache
    service.stop()


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


def test_capture_rechecks_application_identity_after_gpu_readback(monkeypatch):
    import ctypes

    from docking.platform.backends.windows import capture as module

    stream = module.WindowsCaptureStream.__new__(module.WindowsCaptureStream)
    stream._closed = False
    stream._resize_pending = False
    stream._device = SimpleNamespace(current_window=lambda *_args: True)
    stream.hwnd, stream.pid = 100, 101
    stream.pool = ctypes.c_void_p(123)
    stream._validate = MagicMock(side_effect=[True, False])
    closed, released = [], []

    def native_method(pointer, index, result, *arguments):
        assert index == 7
        return lambda pool, output: setattr(output._obj, "value", 456) or 0

    monkeypatch.setattr(module, "method", native_method)
    monkeypatch.setattr(module, "_close", lambda pointer: closed.append(pointer.value))
    monkeypatch.setattr(
        module, "release", lambda pointer: released.append(pointer.value)
    )
    stream._read_frame = lambda _frame: CapturePixels(bytes((255, 0, 0, 255)), 1, 1)
    assert stream.read() is None
    assert closed == released == [456]
    assert stream._validate.call_count == 2
