"""Shell data-object ABI, copy intent and ownership of native allocations."""

import ctypes
import struct
from contextlib import nullcontext
from pathlib import PureWindowsPath

import pytest

from docking.platform.native.windows import drag


@pytest.mark.parametrize("failure", [None, "parse", "create", "get", "size", "lock"])
def test_native_file_formats_copy_shell_data_and_release_all_owned_resources(
    tmp_path, monkeypatch, failure
):
    payload = b"native-shell-file-list"
    buffer = ctypes.create_string_buffer(payload)
    events = []
    parsed = []
    monkeypatch.setattr(drag, "apartment", lambda: nullcontext(object()))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **_kw: name, raising=False)
    monkeypatch.setattr(
        ctypes, "WinError", lambda _code: OSError("Native error"), raising=False
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        drag, "release", lambda value: events.append(("release", value.value))
    )

    def bind(_library, name, *_signature):
        if name == "CoTaskMemFree":
            return lambda value: events.append(("free", value.value))
        if name == "SHParseDisplayName":

            def parse(target, _context, output, _attributes, _result):
                assert "/" not in target
                parsed.append(target)
                output._obj.value = len(parsed) * 10
                return -1 if failure == "parse" else 0

            return parse
        if name == "ILFindLastID":
            return lambda _value: 30
        if name == "SHCreateDataObject":

            def create(parent, count, children, inner, _interface, output):
                assert parent.value == 10 and count == 1 and children[0] == 30
                assert inner is None
                output._obj.value = 40
                return -1 if failure == "create" else 0

            return create
        if name == "RegisterClipboardFormatW":
            return lambda value: 0xC051 if value == "Shell IDList Array" else 0
        if name == "GlobalSize":
            return lambda _value: 2000000 if failure == "size" else len(payload)
        if name == "GlobalLock":
            return lambda _value: 0 if failure == "lock" else ctypes.addressof(buffer)
        if name == "GlobalUnlock":
            return lambda value: events.append(("unlock", value))
        assert name == "ReleaseStgMedium"
        return lambda value: events.append(("medium", value._obj.data))

    def method(pointer, index, *_signature):
        assert pointer.value == 40 and index == 3

        def get(_this, request, output):
            assert request._obj.format == 0xC051
            assert request._obj.aspect == 1 and request._obj.index == -1
            assert request._obj.medium == 1
            output._obj.kind = 1
            output._obj.data = 50
            return -1 if failure == "get" else 0

        return get

    monkeypatch.setattr(drag, "bind", bind)
    monkeypatch.setattr(drag, "method", method)
    if failure is None:
        assert drag.file_drag_formats(tmp_path / "résumé 日本語.lnk") == (
            ("Shell IDList Array", payload),
            (
                "CF_HDROP",
                struct.pack("<IiiII", 20, 0, 0, 0, 1)
                + (
                    str(PureWindowsPath(tmp_path / "résumé 日本語.lnk")).replace(
                        "/", "\\"
                    )
                    + "\0\0"
                ).encode("utf-16-le"),
            ),
            ("Preferred DropEffect", b"\x01\x00\x00\x00"),
        )
        assert events == [
            ("unlock", 50),
            ("medium", 50),
            ("release", 40),
            ("free", 20),
            ("free", 10),
        ]
    else:
        with pytest.raises(OSError):
            drag.file_drag_formats(tmp_path / "résumé 日本語.lnk")
        if failure == "parse":
            assert events == [("free", 10)]
        elif failure == "create":
            assert events == [("release", 40), ("free", 20), ("free", 10)]
        else:
            assert events == [
                ("medium", 50),
                ("release", 40),
                ("free", 20),
                ("free", 10),
            ]


def test_data_object_structures_match_the_windows_sdk_layout():
    wide = ctypes.sizeof(ctypes.c_void_p) == 8
    assert ctypes.sizeof(drag.FormatEtc) == (32 if wide else 20)
    assert ctypes.sizeof(drag.StorageMedium) == (24 if wide else 12)
