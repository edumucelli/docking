"""Shell file-copy formats prepared for the native OLE data object."""

from __future__ import annotations

import ctypes
import struct
from contextlib import ExitStack
from pathlib import Path, PureWindowsPath

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
    file_drop_bytes,
)
from docking.platform.native.windows.ffi import bind


def _check(status: int) -> None:
    if status < 0:
        raise OSError(status & 0xFFFFFFFF, "Could not prepare Shell file data")


def file_drag_formats(path: Path) -> tuple[tuple[str, bytes], ...]:
    """Copy the Shell's own file-list representation on the metadata worker."""
    with apartment() as ole, ExitStack() as stack:
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        parse = bind(
            shell,
            "SHParseDisplayName",
            HRESULT,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        )
        free = bind(ole, "CoTaskMemFree", None, ctypes.c_void_p)
        pointers = []
        for target in (path.parent, path):
            pidl = ctypes.c_void_p()
            status = parse(
                str(PureWindowsPath(target.resolve())).replace("/", "\\"),
                None,
                ctypes.byref(pidl),
                0,
                None,
            )
            if pidl.value:
                stack.callback(free, pidl)
            _check(status)
            if not pidl.value:
                raise OSError("Missing Shell file identity")
            pointers.append(pidl)
        child = bind(shell, "ILFindLastID", ctypes.c_void_p, ctypes.c_void_p)(
            pointers[1]
        )
        if not child:
            raise OSError("Missing Shell child identity")
        items = (ctypes.c_void_p * 1)(child)
        data_object = ctypes.c_void_p()
        create = bind(
            shell,
            "SHCreateDataObject",
            HRESULT,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        interface = GUID.parse("0000010e-0000-0000-c000-000000000046")
        status = create(
            pointers[0],
            1,
            items,
            None,
            ctypes.byref(interface),
            ctypes.byref(data_object),
        )
        stack.callback(release, data_object)
        _check(status)
        if not data_object.value:
            raise OSError("Missing Shell file data object")
        clipboard_format = bind(
            ctypes.WinDLL("user32", use_last_error=True),
            "RegisterClipboardFormatW",
            ctypes.c_uint32,
            ctypes.c_wchar_p,
        )("Shell IDList Array")
        if not clipboard_format:
            raise ctypes.WinError(ctypes.get_last_error())
        request = FormatEtc(clipboard_format, None, 1, -1, 1)
        medium = StorageMedium()
        try:
            _check(
                method(
                    data_object,
                    3,
                    HRESULT,
                    ctypes.POINTER(FormatEtc),
                    ctypes.POINTER(StorageMedium),
                )(data_object, ctypes.byref(request), ctypes.byref(medium))
            )
            if medium.kind != 1 or not medium.data:
                raise OSError("Shell file list is not global-memory data")
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            length = bind(kernel, "GlobalSize", ctypes.c_size_t, ctypes.c_void_p)(
                medium.data
            )
            if not 12 <= length <= 1048576:
                raise OSError("Invalid Shell file data length")
            pointer = bind(kernel, "GlobalLock", ctypes.c_void_p, ctypes.c_void_p)(
                medium.data
            )
            if not pointer:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                data = ctypes.string_at(pointer, length)
            finally:
                bind(kernel, "GlobalUnlock", ctypes.c_int32, ctypes.c_void_p)(
                    medium.data
                )
        finally:
            if medium.kind:
                bind(ole, "ReleaseStgMedium", None, ctypes.POINTER(StorageMedium))(
                    ctypes.byref(medium)
                )
    return (
        ("Shell IDList Array", data),
        (
            "CF_HDROP",
            file_drop_bytes((str(PureWindowsPath(path.resolve())).replace("/", "\\"),)),
        ),
        ("Preferred DropEffect", struct.pack("<I", 1)),
    )
