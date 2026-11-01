"""Cached Shell drag payloads transfer global-memory ownership exactly once."""

import ctypes
import struct
import sys
from contextlib import nullcontext

import pytest

from docking.platform.native.windows import dataobject as native
from docking.platform.native.windows.com import HRESULT, method
from docking.platform.native.windows.ffi import bind


def test_unicode_file_drop_encodes_the_whole_ordered_cross_folder_selection():
    paths = (
        r"C:\Owned one\résumé_日本語_📄.txt",
        r"D:\Other folder\literal & #100% 'quotes'.md",
        r"\\fileserver\team\third document.txt",
    )
    data = native.file_drop_bytes(paths)
    assert struct.unpack("<IiiII", data[:20]) == (20, 0, 0, 0, 1)
    assert data[20:].decode("utf-16-le").split("\0") == [*paths, "", ""]


@pytest.mark.parametrize("paths", [(), ("",), ("C:\\bad\0.txt",)])
def test_incomplete_file_drop_selection_is_rejected(paths):
    with pytest.raises(ValueError):
        native.file_drop_bytes(paths)


@pytest.mark.parametrize(
    "failure",
    [None, "create", "interrupt-create", "register", "allocate", "lock", "set"],
)
def test_cached_object_owns_successful_transfers_and_frees_partial_failures(
    monkeypatch, failure
):
    buffers, transferred, freed, registered = {}, set(), [], []
    released = []
    monkeypatch.setattr(native, "apartment", lambda: nullcontext())
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **_kw: name, raising=False)
    monkeypatch.setattr(
        ctypes, "WinError", lambda _code: OSError("Native error"), raising=False
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)

    def free(handle):
        freed.append(handle)
        del buffers[handle]

    def release(pointer):
        released.append(pointer.value)
        for handle in transferred:
            free(handle)

    def bind(_library, name, *_signature):
        if name == "SHCreateDataObject":

            def create(parent, count, children, inner, _interface, output):
                assert (
                    parent is None and count == 0 and children is None and inner is None
                )
                output._obj.value = 11
                if failure == "interrupt-create":
                    raise KeyboardInterrupt
                return -1 if failure == "create" else 0

            return create
        if name == "RegisterClipboardFormatW":

            def register(value):
                registered.append(value)
                return 0 if failure == "register" else 0xC052

            return register
        if name == "GlobalAlloc":

            def allocate(flags, length):
                assert flags == 0x42
                if failure == "allocate":
                    return 0
                handle = len(freed) + len(buffers) + 100
                buffers[handle] = ctypes.create_string_buffer(length)
                return handle

            return allocate
        if name == "GlobalLock":
            return lambda handle: (
                0 if failure == "lock" else ctypes.addressof(buffers[handle])
            )
        if name == "GlobalUnlock":
            return lambda _handle: 1
        assert name == "GlobalFree", (
            "The cached object must not parse paths or read files"
        )
        return free

    def method(pointer, index, *_signature):
        assert pointer.value == 11 and index == 7

        def set_data(_this, request, medium, owns_memory):
            handle = medium._obj.data
            assert medium._obj.kind == 1 and owns_memory
            assert request._obj.format in (15, 0xC052)
            assert request._obj.aspect == 1 and request._obj.index == -1
            expected = b"file-list" if request._obj.format == 0xC052 else b"file-path"
            assert bytes(buffers[handle]) == expected
            if failure == "set" and request._obj.format == 15:
                return -1
            transferred.add(handle)
            return 0

        return set_data

    monkeypatch.setattr(native, "bind", bind)
    monkeypatch.setattr(native, "release", release)
    monkeypatch.setattr(native, "method", method)
    formats = (("Shell IDList Array", b"file-list"), ("CF_HDROP", b"file-path"))
    if failure is None:
        with native.cached_data_object(formats) as pointer:
            assert pointer.value == 11 and len(buffers) == 2
            assert freed == []
    else:
        expected = KeyboardInterrupt if failure == "interrupt-create" else OSError
        with pytest.raises(expected), native.cached_data_object(formats):
            pytest.fail("An incomplete object must not escape")
    assert not buffers
    assert len(freed) == len(set(freed))
    assert released == [11]
    assert "CF_HDROP" not in registered


@pytest.mark.skipif(sys.platform != "win32", reason="Native Shell data-object ABI")
@pytest.mark.parametrize("directories", [("",), ("", ""), ("first", "different")])
def test_native_cached_file_data_round_trips_through_the_shell(tmp_path, directories):
    filenames = []
    for index, directory in enumerate(directories):
        path = tmp_path / directory / f"Transfer {index} résumé 日本語.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("Owned native format fixture", encoding="utf-8")
        filenames.append(str(path).replace("/", "\\"))
    payload = native.file_drop_bytes(filenames)
    with native.cached_data_object((("CF_HDROP", payload),)) as pointer:
        request = native.FormatEtc(15, None, 1, -1, 1)
        medium = native.StorageMedium()
        assert (
            method(
                pointer,
                3,
                HRESULT,
                ctypes.POINTER(native.FormatEtc),
                ctypes.POINTER(native.StorageMedium),
            )(pointer, ctypes.byref(request), ctypes.byref(medium))
            >= 0
        )
        try:
            assert medium.kind == 1 and medium.data
            query = bind(
                ctypes.WinDLL("shell32"),
                "DragQueryFileW",
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_wchar_p,
                ctypes.c_uint32,
            )
            assert query(medium.data, 0xFFFFFFFF, None, 0) == len(filenames)
            for index, filename in enumerate(filenames):
                length = query(medium.data, index, None, 0)
                result = ctypes.create_unicode_buffer(length + 1)
                assert query(medium.data, index, result, len(result)) == length
                assert result.value == filename
        finally:
            bind(
                ctypes.WinDLL("ole32"),
                "ReleaseStgMedium",
                None,
                ctypes.POINTER(native.StorageMedium),
            )(ctypes.byref(medium))
