"""Window property ABI, return values and COM resource ownership."""

import ctypes
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from docking.platform.backends.windows import properties


def reader(monkeypatch, *, failure=None, value="Fixture.App", kind=31):
    shell, ole = Mock(), Mock()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kw: shell, raising=False)
    events = []

    @contextmanager
    def apartment():
        events.append("enter")
        try:
            yield ole
        finally:
            events.append("exit")

    def store(window, iid, output):
        assert window == 0x100000001
        assert bytes(iid._obj) == bytes(properties.PROPERTY_STORE)
        output._obj.value = 123
        return -1 if failure == "store" else 0

    def get(pointer, key, output):
        assert key._obj.id == 5
        assert bytes(key._obj.format) == bytes(properties.APP_ID.format)
        output._obj.vt = kind
        output._obj.data.string = value
        if failure == "exception":
            raise OSError("Window disappeared")
        return -1 if failure == "get" else 0x40001 if failure == "canonical" else 0

    def clear(output):
        events.append("clear")
        output._obj.data.string = None
        return -1 if failure == "clear" else 0

    shell.SHGetPropertyStoreForWindow.side_effect = store
    ole.PropVariantClear.side_effect = clear
    native_method = Mock(return_value=get)
    monkeypatch.setattr(properties, "apartment", apartment)
    monkeypatch.setattr(properties, "method", native_method)
    monkeypatch.setattr(properties, "release", lambda pointer: events.append("release"))
    return properties.WindowsWindowProperties(), events, native_method, shell


@pytest.mark.parametrize(
    "failure,value,kind,expected",
    [
        (None, "Fixture.App", 31, "Fixture.App"),
        ("canonical", "Fixture.App", 31, "Fixture.App"),
        ("store", None, 0, None),
        ("get", "Fixture.App", 31, None),
        (None, None, 0, None),
        (None, None, 3, None),
        (None, "", 31, None),
        (None, "X" * 129, 31, None),
    ],
)
def test_property_reader_accepts_explicit_app_id_and_always_cleans_up(
    monkeypatch, failure, value, kind, expected
):
    api, events, native_method, shell = reader(
        monkeypatch, failure=failure, value=value, kind=kind
    )
    assert api.app_id(0x100000001) == expected
    assert events == ["enter", "clear", "release", "exit"]
    assert shell.SHGetPropertyStoreForWindow.restype is properties.HRESULT
    assert ctypes.sizeof(properties.PropertyKey) == 20
    assert ctypes.sizeof(properties.PropertyVariant) == (
        24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16
    )
    if failure != "store":
        assert native_method.call_args.args[1:3] == (5, properties.HRESULT)


@pytest.mark.parametrize("failure", ["exception", "clear"])
def test_property_failures_still_release_the_store_and_apartment(monkeypatch, failure):
    api, events, _native_method, _shell = reader(monkeypatch, failure=failure)
    with pytest.raises(OSError):
        api.app_id(0x100000001)
    assert events == ["enter", "clear", "release", "exit"]
