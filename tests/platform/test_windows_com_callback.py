"""Native-owned callback thunks outlive their Python creator safely."""

import ctypes
import gc
import weakref
from unittest.mock import Mock

import pytest

from docking.platform.backends.windows.com import GUID, HRESULT, method
from docking.platform.backends.windows.com_callback import ComCallback

IID = "4e8a3389-c9d8-4bd2-b6b5-124fee6cc14d"


@pytest.fixture(autouse=True)
def abi(monkeypatch):
    # cdecl models the Windows x64 unified calling convention on this host.
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)


def test_native_reference_keeps_callback_alive_after_creator_closes():
    received, failures = [], []
    callback = ComCallback(
        IID, (ctypes.c_int32,), received.append, on_error=failures.append
    )
    pointer = callback.pointer
    method(pointer, 1, ctypes.c_uint32)(pointer)
    callback.close()
    reference = weakref.ref(callback)
    del callback
    gc.collect()
    assert reference() is not None
    assert method(pointer, 3, HRESULT, ctypes.c_int32)(pointer, 7) == 0
    assert received == [7] and failures == []
    assert method(pointer, 2, ctypes.c_uint32)(pointer) == 0
    gc.collect()
    assert reference() is None


def test_query_only_exposes_the_exact_delegate_and_iunknown():
    callback = ComCallback(IID, (), lambda: None, on_error=Mock())
    try:
        iid = GUID.parse("11111111-1111-1111-1111-111111111111")
        output = ctypes.c_void_p(1)
        query = method(
            callback.pointer,
            0,
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        assert (
            query(callback.pointer, ctypes.byref(iid), ctypes.byref(output))
            == -2147467262
        )
        assert output.value is None
        iid = GUID.parse(IID)
        assert query(callback.pointer, ctypes.byref(iid), ctypes.byref(output)) == 0
        assert output.value == callback.pointer.value
        assert method(output, 2, ctypes.c_uint32)(output) == 1
    finally:
        callback.close()


def test_callback_failure_never_escapes_native_abi():
    failure = OSError("Native operation failed")
    errors = []
    callback = ComCallback(IID, (), Mock(side_effect=failure), on_error=errors.append)
    try:
        assert method(callback.pointer, 3, HRESULT)(callback.pointer) == -2147467259
        assert errors == [failure]
    finally:
        callback.close()
