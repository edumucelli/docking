"""Exercise bounded Xlib reads, including failure and ownership paths."""

import ctypes
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.backends.diagnostics import IdentityHintStatus
from docking.platform.backends.x11.impl.identity_hints import (
    IDENTITY_PROPERTIES,
    X11IdentityHintReader,
    _X11Connection,
)


def _reader(
    *,
    raw=b"org.example.App",
    actual_type=10,
    actual_format=8,
    bytes_after=0,
    status=0,
    x_error=0,
):
    reader = X11IdentityHintReader()
    buffer = ctypes.create_string_buffer(raw)
    atoms = {
        IDENTITY_PROPERTIES[0]: 1,
        IDENTITY_PROPERTIES[1]: 2,
        "UTF8_STRING": 10,
        "STRING": 11,
    }

    def read(
        _display,
        _xid,
        _atom,
        offset,
        length,
        delete,
        requested_type,
        type_out,
        format_out,
        count_out,
        after_out,
        data_out,
    ):
        assert (offset, length, delete, requested_type) == (0, 1024, 0, 0)
        ctypes.cast(type_out, ctypes.POINTER(ctypes.c_ulong))[0] = actual_type
        ctypes.cast(format_out, ctypes.POINTER(ctypes.c_int))[0] = actual_format
        ctypes.cast(count_out, ctypes.POINTER(ctypes.c_ulong))[0] = len(raw)
        ctypes.cast(after_out, ctypes.POINTER(ctypes.c_ulong))[0] = bytes_after
        ctypes.cast(data_out, ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)))[0] = (
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        )
        return status

    xlib = SimpleNamespace(
        XInternAtom=MagicMock(side_effect=lambda _d, name, _only: atoms[name.decode()]),
        XGetWindowProperty=MagicMock(side_effect=read),
        XFree=MagicMock(),
    )
    display = SimpleNamespace(
        error_trap_push=MagicMock(),
        error_trap_pop=MagicMock(return_value=x_error),
    )
    reader._connection = _X11Connection(xlib, display, ctypes.c_void_p(1))
    return reader


@pytest.mark.parametrize(
    "raw,actual_type,value",
    [
        ("org.example.É".encode(), 10, "org.example.É"),
        (b"app\0", 11, "app"),
        (b"caf\xe9", 11, "café"),
        (b"", 10, ""),
    ],
)
def test_valid_identity_strings_are_read_and_freed(raw, actual_type, value):
    reader = _reader(raw=raw, actual_type=actual_type)
    hints = reader.read(42)
    assert all(hint.status is IdentityHintStatus.PRESENT for hint in hints)
    assert all(hint.value == value for hint in hints)
    assert reader._connection.xlib.XFree.call_count == 2
    assert reader._connection.display.error_trap_pop.call_count == 2
    atom_calls = reader._connection.xlib.XInternAtom.call_count
    reader.read(42)
    assert reader._connection.xlib.XInternAtom.call_count == atom_calls


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"actual_type": 0}, IdentityHintStatus.ABSENT),
        ({"actual_type": 99}, IdentityHintStatus.MALFORMED),
        ({"actual_format": 32}, IdentityHintStatus.MALFORMED),
        ({"raw": b"\xff"}, IdentityHintStatus.MALFORMED),
        ({"raw": b"a\0b"}, IdentityHintStatus.MALFORMED),
        ({"raw": b"a" * 4097}, IdentityHintStatus.OVERSIZED),
        ({"bytes_after": 1}, IdentityHintStatus.OVERSIZED),
        ({"status": 1}, IdentityHintStatus.READ_ERROR),
        ({"x_error": 3}, IdentityHintStatus.READ_ERROR),
    ],
)
def test_invalid_or_disappearing_properties_are_isolated_and_freed(kwargs, expected):
    reader = _reader(**kwargs)
    hints = reader.read(42)
    assert all(hint.status is expected and not hint.value for hint in hints)
    assert reader._connection.xlib.XFree.call_count == 2
    assert reader._connection.display.error_trap_pop.call_count == 2


def test_missing_atoms_are_not_cached_or_read():
    reader = _reader()
    reader._connection.xlib.XInternAtom.side_effect = None
    reader._connection.xlib.XInternAtom.return_value = 0
    assert all(hint.status is IdentityHintStatus.ABSENT for hint in reader.read(42))
    reader._connection.xlib.XGetWindowProperty.assert_not_called()
    reader._connection.xlib.XFree.assert_not_called()
    reader.read(42)
    assert reader._connection.xlib.XInternAtom.call_count == 4


def test_unavailable_display_does_not_read_properties(monkeypatch):
    reader = X11IdentityHintReader()
    monkeypatch.setattr(reader, "_initialize", lambda: None)
    assert all(
        hint.status is IdentityHintStatus.UNAVAILABLE for hint in reader.read(42)
    )


def test_unexpected_binding_failure_does_not_abort_capture():
    reader = _reader()
    reader._connection.xlib.XGetWindowProperty.side_effect = RuntimeError("PRIVATE")
    assert all(hint.status is IdentityHintStatus.READ_ERROR for hint in reader.read(42))
    assert reader._connection.display.error_trap_pop.call_count == 2
