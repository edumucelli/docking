"""Synthetic owned dialog for pairing-helper lifecycle tests; no radio calls."""

from __future__ import annotations

import argparse
import ctypes

from docking.applets.bluetooth.windows_pairing import serve
from docking.applets.bluetooth.windows_pairing_ui import pairing_prompt
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ceremony", choices=("pin", "display", "match"))
    args = parser.parse_args()
    if args.ceremony:
        kind = {"pin": 4, "display": 2, "match": 8}[args.ceremony]

        def ceremony(owner, _radio, _device):
            answer = pairing_prompt(owner, kind, "246810")
            accepted = answer == "135790" if kind == 4 else answer is not None
            return 0 if accepted else 1223

        serve(ceremony)
    else:
        serve(prompt)
