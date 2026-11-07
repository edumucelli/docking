"""Windows ABI, DWORD wrap, lifecycle ownership and session service wiring."""

import ctypes
from unittest.mock import Mock

import pytest

from docking.platform.backends.windows import idle
from docking.platform.backends.windows.session import WindowsSessionBackend


@pytest.mark.parametrize(
    "last,now,expected",
    [
        (0, 0, 0.0),
        (10000, 10000, 0.0),
        (1500, 4500, 3.0),
        (0xFFFFFFF0, 0x100000010, 0.032),
        (0x10, 0x100000020, 0.016),
        (5000, 4999, None),
        (-1, 10000, None),
        (0x100000000, 10000, None),
        (0, -1, None),
        (0, 0x10000000000000000, None),
    ],
)
def test_idle_duration_uses_native_ticks_and_handles_wrap(last, now, expected):
    assert idle.idle_interval(last, now) == expected


def test_native_layout_and_size_are_fixed_width_on_both_hosts(monkeypatch):
    user, kernel = Mock(), Mock()
    kernel.GetTickCount64.return_value = 10000

    def last(pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(idle.LastInputInfo)).contents
        assert info.size == 8
        info.tick = 9000
        return 1

    user.GetLastInputInfo.side_effect = last
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda name, **kwargs: user if name == "user32" else kernel,
        raising=False,
    )
    assert ctypes.sizeof(idle.LastInputInfo) == 8
    assert idle.LastInputInfo.tick.offset == 4
    assert idle.WindowsInputAPI().sample() == (9000, 10000)


def test_creation_start_and_stop_do_not_poll_or_load_native_libraries(monkeypatch):
    native = Mock(side_effect=OSError("unavailable"))
    monkeypatch.setattr(ctypes, "WinDLL", native, raising=False)
    service = idle.WindowsIdleService()
    service.start()
    service.stop()
    assert service.idle_seconds() is None
    native.assert_not_called()
    service.start()
    assert service.idle_seconds() is None
    native.assert_called_once()


def test_failed_or_stopped_read_is_unknown_and_restart_uses_fresh_input():
    api = Mock()
    api.sample.side_effect = [OSError("query failed"), (1000, 4000)]
    service = idle.WindowsIdleService(api=api)
    assert service.idle_seconds() is None
    service.stop()
    assert service.idle_seconds() is None and api.sample.call_count == 1
    service.start()
    assert service.idle_seconds() == 3


@pytest.mark.parametrize("restart", [False, True])
def test_read_started_before_stop_cannot_deliver_into_a_later_lifetime(restart):
    api = Mock()
    service = idle.WindowsIdleService(api=api)

    def read():
        service.stop()
        if restart:
            service.start()
        return 1000, 4000

    api.sample.side_effect = read
    assert service.idle_seconds() is None


def test_windows_session_advertises_and_owns_the_common_idle_service(monkeypatch):
    from docking.platform.backends.windows import previews, surface, visibility, windows

    timeline = Mock()
    for module, name, label in (
        (windows, "WindowsWindowService", "windows"),
        (previews, "WindowsPreviewService", "previews"),
        (surface, "WindowsSurfaceService", "surface"),
        (visibility, "WindowsVisibilityService", "visibility"),
    ):
        service = getattr(timeline, label)
        monkeypatch.setattr(module, name, Mock(return_value=service))
    monkeypatch.setattr(idle, "WindowsIdleService", Mock(return_value=timeline.idle))
    backend = WindowsSessionBackend(
        model=Mock(),
        config=Mock(),
        application_registry=Mock(),
        process_identity_service=Mock(),
    )
    assert backend.idle is timeline.idle and backend.capabilities.supports_idle_time
    backend.start()
    timeline.idle.start.assert_called_once()
    backend.stop()
    timeline.idle.stop.assert_called_once()
    names = [call[0] for call in timeline.mock_calls]
    assert names.index("idle.stop") < names.index("surface.stop")
