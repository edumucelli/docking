"""Synthetic owned dialog for pairing-helper lifecycle tests; no radio calls."""

from __future__ import annotations

import ctypes

from docking.applets.bluetooth.windows_pairing import serve
from docking.platform.backends.windows.ffi import bind


def prompt(owner: int, _radio: int, _device: int) -> int:
    message = bind(
        ctypes.WinDLL("user32", use_last_error=True),
        "MessageBoxW",
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    )
    result = message(
        owner,
        "Synthetic prompt for Docking pairing lifecycle tests. No radio is used.",
        "Docking pairing fixture",
        1,
    )
    return 0 if result == 1 else 1223


if __name__ == "__main__":
    serve(prompt)
