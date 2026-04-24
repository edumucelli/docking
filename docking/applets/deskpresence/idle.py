"""X11 idle-time probe for the desk-presence applet.

Uses the XScreenSaver extension via ctypes (libXss). The extension reports
how long it has been since the last input event; we read that value every
few seconds to decide whether the user is currently at the desk.

Returns ``None`` in environments where the extension is unavailable
(e.g. headless CI without an X server, or a display without libXss). The
caller treats ``None`` as "unknown" and does not credit any bucket.
"""

from __future__ import annotations

import ctypes
from typing import ClassVar

from docking.log import get_logger

log = get_logger("deskpresence.idle")


class _XScreenSaverInfo(ctypes.Structure):
    _fields_: ClassVar = [
        ("window", ctypes.c_ulong),
        ("state", ctypes.c_int),
        ("kind", ctypes.c_int),
        ("til_or_since", ctypes.c_ulong),
        ("idle", ctypes.c_ulong),
        ("event_mask", ctypes.c_ulong),
    ]


_xlib: ctypes.CDLL | None = None
_xss: ctypes.CDLL | None = None
_loaded = False


def _load_libraries() -> bool:
    """Load libX11 and libXss once per process."""
    global _xlib, _xss, _loaded
    if _loaded:
        return _xlib is not None and _xss is not None
    _loaded = True
    try:
        _xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
        _xss = ctypes.cdll.LoadLibrary("libXss.so.1")
    except OSError as exc:
        log.debug("XScreenSaver extension unavailable: %s", exc)
        _xlib = None
        _xss = None
        return False

    _xlib.XDefaultRootWindow.restype = ctypes.c_ulong
    _xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    _xlib.XFree.argtypes = [ctypes.c_void_p]
    _xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XScreenSaverInfo)
    _xss.XScreenSaverQueryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_XScreenSaverInfo),
    ]
    _xss.XScreenSaverQueryInfo.restype = ctypes.c_int
    return True


def _xdisplay_handle() -> ctypes.c_void_p | None:
    """Resolve the current X11 display as a ctypes void pointer."""
    try:
        import gi

        gi.require_version("GdkX11", "3.0")
        from gi.repository import GdkX11
    except (ImportError, ValueError) as exc:
        log.debug("GdkX11 unavailable: %s", exc)
        return None
    display = GdkX11.X11Display.get_default()
    if display is None:
        return None
    return ctypes.c_void_p(hash(display.get_xdisplay()))


def get_idle_ms() -> int | None:
    """Return idle time in milliseconds, or ``None`` if unavailable."""
    if not _load_libraries():
        return None
    assert _xlib is not None
    assert _xss is not None
    xdisplay = _xdisplay_handle()
    if xdisplay is None:
        return None
    root = _xlib.XDefaultRootWindow(xdisplay)
    info_ptr = _xss.XScreenSaverAllocInfo()
    if not info_ptr:
        return None
    try:
        ok = _xss.XScreenSaverQueryInfo(xdisplay, root, info_ptr)
        if not ok:
            return None
        return int(info_ptr.contents.idle)
    finally:
        _xlib.XFree(info_ptr)
