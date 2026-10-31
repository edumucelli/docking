"""Read exact directory association facts without resolving by executable name."""

import ctypes

from .com import HRESULT
from .ffi import bind


def directory_association(*, name: bool) -> str | None:
    """Query the Directory open verb, including its documented Folder base."""
    query = bind(
        ctypes.WinDLL("shlwapi", use_last_error=True),
        "AssocQueryStringW",
        HRESULT,
        ctypes.c_uint32,
        ctypes.c_int32,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32),
    )
    # DEFAULTTOFOLDER, NOFIXUPS, IGNOREUNKNOWN: respect user settings and do
    # not let an unknown association silently select an unrelated executable.
    flags, field = 0x508, 4 if name else 21  # FRIENDLYAPPNAME / APPID
    size = ctypes.c_uint32()
    status = query(flags, field, "Directory", "open", None, ctypes.byref(size))
    if status & 0xFFFFFFFF in (0x80070002, 0x80070003, 0x80070483):
        return None
    if status != 1 or not 0 < size.value <= 32768:
        raise OSError(status & 0xFFFFFFFF, "Directory association size unavailable")
    capacity = size.value
    value = ctypes.create_unicode_buffer(capacity)
    status = query(flags, field, "Directory", "open", value, ctypes.byref(size))
    if status != 0 or not 0 < size.value <= capacity:
        raise OSError(status & 0xFFFFFFFF, "Directory association changed or failed")
    return str(value.value) if value.value else None
