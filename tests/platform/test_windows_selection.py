"""Complete native Shell selections, format identity and allocation ownership."""

import ctypes
import struct
import sys
from contextlib import ExitStack, nullcontext
from pathlib import PureWindowsPath

import pytest

from docking.platform.native.windows import selection
from docking.platform.native.windows.com import (
    GUID,
    HRESULT,
    apartment,
    method,
    release,
)
from docking.platform.native.windows.dataobject import (
    FormatEtc,
    StorageMedium,
    cached_data_object,
    file_drop_bytes,
)
from docking.platform.native.windows.ffi import bind


def pidl(*items):
    return b"".join(struct.pack("<H", len(item) + 2) + item for item in items) + b"\0\0"


def unpack_cida(data):
    count = struct.unpack_from("<I", data)[0]
    offsets = struct.unpack_from("<" + "I" * (count + 1), data, 4)
    assert offsets[0] == 4 * (count + 2)
    return tuple(
        data[start:end]
        for start, end in zip(offsets, (*offsets[1:], len(data)), strict=True)
    )


class Shell:
    def __init__(self, monkeypatch, identifiers, *, failure=None):
        self.identifiers = identifiers
        self.failure = failure
        self.buffers, self.parsed, self.freed, self.copied = [], [], [], []
        self.addresses = []
        monkeypatch.setattr(selection, "apartment", lambda: nullcontext(object()))
        monkeypatch.setattr(selection, "bind", self.bind)
        monkeypatch.setattr(
            ctypes, "WinDLL", lambda *_args, **_kw: object(), raising=False
        )
        copy = ctypes.string_at

        def string_at(pointer, length):
            self.copied.append(pointer.value)
            return copy(pointer, length)

        monkeypatch.setattr(ctypes, "string_at", string_at)

    def bind(self, _library, name, *_signature):
        if name == "CoTaskMemFree":
            return lambda value: self.freed.append(value.value)
        if name == "ILGetSize":

            def size(value):
                if self.failure == "size":
                    return selection._MAX_SELECTION_BYTES + 1
                return len(self.identifiers[self.addresses.index(value.value)])

            return size
        assert name == "SHParseDisplayName"

        def parse(target, _context, output, _attributes, _result):
            self.parsed.append(target)
            buffer = ctypes.create_string_buffer(self.identifiers[len(self.parsed) - 1])
            self.buffers.append(buffer)
            address = ctypes.addressof(buffer)
            if self.failure != "null":
                output._obj.value = address
                self.addresses.append(address)
            if self.failure == "cancel":
                raise KeyboardInterrupt
            return (
                -2147024891
                if self.failure == "parse"
                else (1 if self.failure == "positive" else 0)
            )

        return parse


@pytest.mark.parametrize(
    "targets",
    [
        (r"C:\Owned\résumé_日本語_📄.txt",),
        (r"C:\Owned\one.txt", r"C:\Owned\two.log"),
        (
            r"C:\One\résumé.txt",
            r"D:\Other\literal & #100%.log",
            r"\\server\team\third.txt",
        ),
    ],
)
def test_file_selection_contains_ordered_complete_desktop_relative_identities(
    monkeypatch, targets
):
    identifiers = tuple(
        pidl(b"desktop", target.encode("utf-16-le")) for target in targets
    )
    shell = Shell(monkeypatch, identifiers)
    formats = selection.shell_selection_formats(targets, files=True)
    assert [name for name, _data in formats] == ["Shell IDList Array", "CF_HDROP"]
    assert unpack_cida(formats[0][1]) == (b"\0\0", *identifiers)
    assert formats[1][1] == file_drop_bytes(targets)
    assert shell.parsed == list(targets)
    assert shell.copied == shell.addresses
    assert shell.freed == list(reversed(shell.addresses))


def test_protocol_selection_has_virtual_shell_identity_and_literal_unicode_url(
    monkeypatch,
):
    uri = "https://example.invalid/r%C3%A9sum%C3%A9?q=100%25#日本語"
    identifier = pidl(b"virtual-internet", uri.encode("utf-16-le"))
    shell = Shell(monkeypatch, (identifier,))
    formats = selection.shell_selection_formats((uri,), files=False)
    assert unpack_cida(formats[0][1]) == (b"\0\0", identifier)
    assert formats[1] == ("UniformResourceLocatorW", (uri + "\0").encode("utf-16-le"))
    assert shell.freed == shell.addresses


@pytest.mark.parametrize("failure", ("parse", "positive", "cancel", "null", "size"))
def test_failed_or_cancelled_native_identity_frees_nonnull_outputs_without_reading(
    monkeypatch, failure
):
    shell = Shell(monkeypatch, (pidl(b"one"),), failure=failure)
    with pytest.raises(KeyboardInterrupt if failure == "cancel" else OSError) as raised:
        selection.shell_selection_formats((r"C:\Owned\one.txt",), files=True)
    if failure == "parse":
        assert raised.value.errno == 0x80070005
    assert shell.freed == shell.addresses
    assert not shell.copied


def test_total_native_identity_bound_is_checked_before_copying_the_next_pidl(
    monkeypatch,
):
    monkeypatch.setattr(selection, "_MAX_SELECTION_BYTES", 32)
    shell = Shell(monkeypatch, (pidl(b"first"), pidl(b"second")))
    with pytest.raises(OSError, match="length"):
        selection.shell_selection_formats((r"C:\one.txt", r"D:\two.log"), files=True)
    assert shell.copied == shell.addresses[:1]
    assert shell.freed == list(reversed(shell.addresses))


@pytest.mark.parametrize(
    "identifier",
    (b"", b"\0\0", b"\x01\0\0\0", b"\x08\0abc\0\0", pidl(b"one") + b"extra"),
)
def test_malformed_cida_identity_is_rejected(identifier):
    with pytest.raises(ValueError):
        selection._shell_id_list((identifier,))


def test_cida_selection_bounds_include_header_and_empty_desktop_parent(monkeypatch):
    identifier = pidl(b"one")
    monkeypatch.setattr(selection, "_MAX_SELECTION_BYTES", 14 + len(identifier))
    assert len(selection._shell_id_list((identifier,))) == 14 + len(identifier)
    monkeypatch.setattr(selection, "_MAX_SELECTION_BYTES", 13 + len(identifier))
    with pytest.raises(OSError, match="byte limit"):
        selection._shell_id_list((identifier,))


@pytest.mark.parametrize(
    "targets,files",
    [
        ((), True),
        (("",), True),
        (("C:\\bad\0.txt",), True),
        (("relative.txt",), True),
        (("https://example.invalid/one", "https://example.invalid/two"), False),
    ],
)
def test_incomplete_selection_is_rejected_before_native_calls(
    monkeypatch, targets, files
):
    def forbidden():
        pytest.fail("Invalid selection must not enter a native apartment")

    monkeypatch.setattr(selection, "apartment", forbidden)
    with pytest.raises(ValueError):
        selection.shell_selection_formats(targets, files=files)


@pytest.mark.skipif(sys.platform != "win32", reason="Native Shell selection ABI")
@pytest.mark.parametrize("directories", [("",), ("", ""), ("first", "different")])
@pytest.mark.parametrize("with_hdrop", (False, True))
def test_native_shell_item_array_recovers_the_exact_ordered_selection(
    tmp_path, directories, with_hdrop
):
    targets = []
    for index, directory in enumerate(directories):
        path = tmp_path / directory / f"Selected {index} résumé 日本語 📄.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("Owned native Shell selection fixture", encoding="utf-8")
        targets.append(str(path.resolve()).replace("/", "\\"))
    formats = selection.shell_selection_formats(targets, files=True)
    if not with_hdrop:
        formats = formats[:1]
    with apartment() as ole, cached_data_object(formats) as data, ExitStack() as stack:
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, expected in formats:
            kind = (
                15
                if name == "CF_HDROP"
                else bind(
                    ctypes.WinDLL("user32"),
                    "RegisterClipboardFormatW",
                    ctypes.c_uint32,
                    ctypes.c_wchar_p,
                )(name)
            )
            assert kind
            request, medium = FormatEtc(kind, None, 1, -1, 1), StorageMedium()
            try:
                assert (
                    method(
                        data,
                        3,
                        HRESULT,
                        ctypes.POINTER(FormatEtc),
                        ctypes.POINTER(StorageMedium),
                    )(data, ctypes.byref(request), ctypes.byref(medium))
                    == 0
                )
                assert medium.kind == 1 and medium.data
                assert bind(kernel, "GlobalSize", ctypes.c_size_t, ctypes.c_void_p)(
                    medium.data
                ) >= len(expected)
                pointer = bind(kernel, "GlobalLock", ctypes.c_void_p, ctypes.c_void_p)(
                    medium.data
                )
                assert pointer
                try:
                    assert ctypes.string_at(pointer, len(expected)) == expected
                finally:
                    bind(kernel, "GlobalUnlock", ctypes.c_int32, ctypes.c_void_p)(
                        medium.data
                    )
            finally:
                if medium.kind:
                    bind(ole, "ReleaseStgMedium", None, ctypes.POINTER(StorageMedium))(
                        ctypes.byref(medium)
                    )
        array = ctypes.c_void_p()
        stack.callback(release, array)
        assert (
            bind(
                shell,
                "SHCreateShellItemArrayFromDataObject",
                HRESULT,
                ctypes.c_void_p,
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p),
            )(
                data,
                ctypes.byref(GUID.parse("b63ea76d-1f85-456f-a19c-48159efa858b")),
                ctypes.byref(array),
            )
            == 0
        )
        assert array.value
        count = ctypes.c_uint32()
        assert (
            method(array, 7, HRESULT, ctypes.POINTER(ctypes.c_uint32))(
                array, ctypes.byref(count)
            )
            == 0
        )
        assert count.value == len(targets)
        for index, expected in enumerate(targets):
            with ExitStack() as item_stack:
                item = ctypes.c_void_p()
                item_stack.callback(release, item)
                assert (
                    method(
                        array,
                        8,
                        HRESULT,
                        ctypes.c_uint32,
                        ctypes.POINTER(ctypes.c_void_p),
                    )(array, index, ctypes.byref(item))
                    == 0
                )
                assert item.value
                path = ctypes.c_void_p()
                try:
                    assert (
                        method(
                            item,
                            5,
                            HRESULT,
                            ctypes.c_uint32,
                            ctypes.POINTER(ctypes.c_void_p),
                        )(item, 0x80058000, ctypes.byref(path))
                        == 0
                    )  # SIGDN_FILESYSPATH
                    assert path.value
                    assert PureWindowsPath(ctypes.wstring_at(path)) == PureWindowsPath(
                        expected
                    )
                finally:
                    if path.value:
                        bind(ole, "CoTaskMemFree", None, ctypes.c_void_p)(path)
