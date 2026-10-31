"""Canonical shell identities for shortcut files, including implicit app IDs."""

from __future__ import annotations

import ctypes
from contextlib import ExitStack
from pathlib import Path, PureWindowsPath

from .com import GUID, HRESULT, apartment, method, release
from .ffi import bind

# IApplicationResolver2's first method is used by the Windows shell for .lnk
# identities. This is an internal interface; unavailable implementations return
# no identity and callers retain their own stable registration ID.
# ABI reference: System Informer, phlib/include/appresolverp.h.
_RESOLVER_CLASS = GUID.parse("660b90c8-73a9-4b58-8cae-355b7f55341b")
_RESOLVER = GUID.parse("de25675a-72de-44b4-9373-05170450c140")
_SHELL_ITEM = GUID.parse("43826d1e-e718-42ee-bc55-a1e261c37bfe")


def shortcut_application_id(path: Path) -> str | None:
    """Read the native ID without guessing from names, commands or directories."""
    with apartment() as ole, ExitStack() as stack:
        resolver, item, value = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        create = bind(
            ole,
            "CoCreateInstance",
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        status = create(
            ctypes.byref(_RESOLVER_CLASS),
            None,
            1,
            ctypes.byref(_RESOLVER),
            ctypes.byref(resolver),
        )
        stack.callback(release, resolver)
        if status < 0 or not resolver.value:
            return None
        parse = bind(
            ctypes.WinDLL("shell32", use_last_error=True),
            "SHCreateItemFromParsingName",
            HRESULT,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        # An active MSYS2 shell renders Path with forward slashes, which this
        # shell parsing boundary does not accept as a filesystem item.
        status = parse(
            str(PureWindowsPath(path)).replace("/", "\\"),
            None,
            ctypes.byref(_SHELL_ITEM),
            ctypes.byref(item),
        )
        stack.callback(release, item)
        if status < 0 or not item.value:
            return None
        try:
            status = method(
                resolver, 3, HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
            )(resolver, item, ctypes.byref(value))
            if status < 0 or not value.value:
                return None
            result = ctypes.wstring_at(value)
            return (
                result
                if 0 < len(result) <= 128 and not any(c.isspace() for c in result)
                else None
            )
        finally:
            if value.value:
                bind(ole, "CoTaskMemFree", None, ctypes.c_void_p)(value)
