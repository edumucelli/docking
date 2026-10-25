"""Activate packaged applications with Windows foreground permission."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from docking.log import get_logger

from .com import GUID, HRESULT, apartment, method, release
from .native import _bind

log = get_logger("windows.shell")


def activate_packaged_application(application_id: str) -> bool:
    """Pass the user's launch privilege to the shell's activation manager."""
    try:
        with apartment() as ole:
            create = _bind(
                ole,
                "CoCreateInstance",
                HRESULT,
                ctypes.POINTER(GUID),
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p),
            )
            clsid = GUID.parse("45ba127d-10a8-46ea-8ab7-56ea9078943c")
            iid = GUID.parse("2e941141-7f97-4756-ba1d-9decde894a3d")
            manager = ctypes.c_void_p()
            status = create(
                ctypes.byref(clsid), None, 4, ctypes.byref(iid), ctypes.byref(manager)
            )
            if status < 0:
                return False
            try:
                allow = _bind(
                    ole,
                    "CoAllowSetForegroundWindow",
                    HRESULT,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                )
                # Windows decides whether this call has foreground privilege.
                # A launch initiated by the user's click normally has it.
                allow(manager, None)
                activate = method(
                    manager,
                    3,
                    HRESULT,
                    wintypes.LPCWSTR,
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD),
                )
                pid = wintypes.DWORD()
                status = activate(manager, application_id, None, 0, ctypes.byref(pid))
                return bool(status >= 0)
            finally:
                release(manager)
    except OSError as exc:
        log.warning("Packaged application activation failed: %s", exc)
        return False
