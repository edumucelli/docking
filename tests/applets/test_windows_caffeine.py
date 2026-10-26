"""Power-request ABI, count ownership, failures and lazy platform selection."""

import ctypes
from unittest.mock import Mock

import pytest

from docking.applets.caffeine import inhibit
from docking.applets.caffeine import windows as mod


@pytest.fixture
def native(monkeypatch):
    kernel = Mock()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        ctypes, "WinError", lambda code: OSError(code, "Denied"), raising=False
    )
    kernel.PowerCreateRequest.return_value = 0x100000001
    kernel.PowerSetRequest.return_value = kernel.PowerClearRequest.return_value = (
        kernel.CloseHandle.return_value
    ) = 1
    return kernel


def test_native_abi_borrows_reason_and_preserves_handle_width(native):
    def create(output):
        context = output._obj
        assert context.version == 0 and context.flags == 1
        assert context.reason.simple == "Keep awake"
        return 0x100000001

    native.PowerCreateRequest.side_effect = create
    api = mod.WindowsPowerRequests()
    handle = api.create("Keep awake")
    assert handle == 0x100000001
    assert mod.ReasonContext.reason.offset == 8
    assert ctypes.sizeof(mod.ReasonContext) == (
        32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24
    )
    assert native.PowerCreateRequest.restype is ctypes.c_void_p
    api.set(handle, 0)
    api.clear(handle, 0)
    api.close(handle)
    native.PowerSetRequest.assert_called_once_with(handle, 0)
    native.PowerClearRequest.assert_called_once_with(handle, 0)
    native.CloseHandle.assert_called_once_with(handle)


@pytest.mark.parametrize("value", [None, ctypes.c_void_p(-1).value])
def test_invalid_native_create_is_not_an_owned_handle(native, value):
    native.PowerCreateRequest.return_value = value
    backend = mod.WindowsInhibitor()
    assert not backend.acquire() and not backend.active
    native.PowerSetRequest.assert_not_called()
    native.CloseHandle.assert_not_called()


def test_acquire_and_release_are_idempotent_with_balanced_native_counts(native):
    backend = mod.WindowsInhibitor()
    assert backend.acquire() and backend.acquire()
    assert backend.active
    assert [c.args for c in native.PowerSetRequest.call_args_list] == [
        (0x100000001, 0),
        (0x100000001, 1),
    ]
    backend.release()
    backend.release()
    assert not backend.active
    assert {c.args for c in native.PowerClearRequest.call_args_list} == {
        (0x100000001, 0),
        (0x100000001, 1),
    }
    native.CloseHandle.assert_called_once_with(0x100000001)


@pytest.mark.parametrize("failure", [0, 1])
def test_partial_acquisition_rolls_back_every_owned_count_and_closes(native, failure):
    native.PowerSetRequest.side_effect = lambda handle, kind: int(kind != failure)
    backend = mod.WindowsInhibitor()
    assert not backend.acquire() and not backend.active
    assert [c.args[1] for c in native.PowerClearRequest.call_args_list] == (
        [0] if failure == 1 else []
    )
    native.CloseHandle.assert_called_once()


def test_clear_failure_still_closes_object_to_release_remaining_requests(native):
    backend = mod.WindowsInhibitor()
    assert backend.acquire()
    native.PowerClearRequest.return_value = 0
    backend.release()
    assert not backend.active and backend._handle is None
    assert native.PowerClearRequest.call_count == 2
    native.CloseHandle.assert_called_once()


def test_failed_close_keeps_ownership_for_retry_without_leaking_a_second_object(native):
    backend = mod.WindowsInhibitor()
    assert backend.acquire()
    native.CloseHandle.return_value = 0
    backend.release()
    assert backend._handle == 0x100000001
    assert not backend.acquire()
    assert native.PowerCreateRequest.call_count == 1
    native.CloseHandle.return_value = 1
    backend.release()
    assert backend._handle is None


def test_missing_library_and_platform_factory_are_lazy(monkeypatch):
    library = Mock(side_effect=OSError("Unavailable"))
    monkeypatch.setattr(ctypes, "WinDLL", library, raising=False)
    monkeypatch.setattr(inhibit.environment, "is_windows", lambda: True)
    backend = inhibit.default_inhibitor()
    assert isinstance(backend, mod.WindowsInhibitor) and not backend.active
    library.assert_not_called()
    backend.release()
    library.assert_not_called()
    assert not backend.acquire()
