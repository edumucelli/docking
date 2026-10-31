"""Battery-service decoding, native error paths and owned resource cleanup."""

import ctypes
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.bluetooth import windows_gatt as mod


@pytest.fixture
def native(monkeypatch):
    state = SimpleNamespace(percent=0, size=1, status=0, closed=[])
    results = iter([11, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29])
    monkeypatch.setattr(
        mod, "object_result", lambda *_args: ctypes.c_void_p(next(results))
    )
    monkeypatch.setattr(mod, "query", lambda _scope, pointer, _iid: pointer)

    def value(pointer, index, kind):
        if index == 6:
            return kind(state.status)
        return kind(state.size if pointer.value == 29 else 1)

    monkeypatch.setattr(mod, "value", value)
    data = (ctypes.c_ubyte * 1)()

    def method(pointer, index, _result, *_arguments):
        if index == 3:

            def bytes_(_pointer, output):
                data[0] = state.percent
                ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)))[
                    0
                ] = ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte))
                return 0

            return bytes_

        def close(_pointer):
            state.closed.append(pointer.value)
            return 0

        return close

    monkeypatch.setattr(mod, "method", method)
    return state, SimpleNamespace(wait=Mock())


@pytest.mark.parametrize(
    "percent,expected", [(0, 0), (100, 100), (101, None), (255, None)]
)
def test_battery_read_preserves_zero_and_rejects_out_of_range(
    native, percent, expected
):
    state, runtime = native
    state.percent = percent
    assert mod.read_battery(runtime, ctypes.c_void_p(10), threading.Event()) == expected
    assert runtime.wait.call_count == 3
    assert state.closed == [22]


@pytest.mark.parametrize("size", [0, 2, 1024])
def test_battery_level_requires_exactly_one_byte(native, size):
    state, runtime = native
    state.size = size
    assert mod.read_battery(runtime, ctypes.c_void_p(10), threading.Event()) is None
    assert state.closed == [22]


def test_failed_gatt_status_does_not_supply_a_battery(native):
    state, runtime = native
    state.status = 3
    assert mod.read_battery(runtime, ctypes.c_void_p(10), threading.Event()) is None
    assert runtime.wait.call_count == 1


def test_cancelled_battery_read_starts_no_operation(native):
    _state, runtime = native
    cancelled = threading.Event()
    cancelled.set()
    assert mod.read_battery(runtime, ctypes.c_void_p(10), cancelled) is None
    runtime.wait.assert_not_called()


def test_battery_query_timeout_is_optional_and_closes_the_service(native):
    state, runtime = native
    runtime.wait.side_effect = [None, TimeoutError("Denied or timed out")]
    assert mod.read_battery(runtime, ctypes.c_void_p(10), threading.Event()) is None
    assert state.closed == [22]
