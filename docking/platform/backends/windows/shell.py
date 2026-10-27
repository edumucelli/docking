"""Activate packaged applications and open native shell targets."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import PureWindowsPath

from docking.log import get_logger

from .com import GUID, HRESULT, apartment, method, release
from .ffi import bind as _bind

log = get_logger("windows.shell")


def activate_application_target(target: str) -> bool:
    """Activate a recorded shell application or native executable shortcut."""
    prefix = "shell:AppsFolder\\"
    if target.startswith(prefix) and target[len(prefix) :]:
        app_id = target[len(prefix) :]
        if "!" in app_id:
            return activate_packaged_application(app_id)
        return open_default_target(target)
    shortcut = PureWindowsPath(target)
    if shortcut.is_absolute() and shortcut.suffix.lower() == ".lnk":
        return open_default_target(target)
    return False


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


def open_default_target(target: str) -> bool:
    """Open a native filename or allowed web URI using its shell association."""
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        return False
    try:
        startfile(target, "open")
        return True
    except (OSError, NotImplementedError) as exc:
        log.warning("Windows shell could not open %s: %s", target, exc)
        return False
