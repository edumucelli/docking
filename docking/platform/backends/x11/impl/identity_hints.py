"""Bounded, read-only reads of X11 application identity properties."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11

from docking.platform.backends.diagnostics import (
    IdentityHintStatus,
    WindowIdentityHint,
)

IDENTITY_PROPERTIES = ("_GTK_APPLICATION_ID", "_KDE_NET_WM_DESKTOP_FILE")
MAX_PROPERTY_BYTES = 4096


@dataclass(frozen=True)
class _X11Connection:
    xlib: ctypes.CDLL
    display: GdkX11.X11Display
    xdisplay: ctypes.c_void_p


class X11IdentityHintReader:
    """Use GTK's X connection; never open or close a separate display."""

    def __init__(self) -> None:
        self._connection: _X11Connection | None = None
        self._atoms: dict[str, int] = {}

    def read(self, xid: int) -> tuple[WindowIdentityHint, ...]:
        try:
            connection = self._initialize()
        except Exception:
            connection = None
        if connection is None:
            return tuple(
                WindowIdentityHint(name, IdentityHintStatus.UNAVAILABLE)
                for name in IDENTITY_PROPERTIES
            )
        hints: list[WindowIdentityHint] = []
        for name in IDENTITY_PROPERTIES:
            try:
                hint = self._read_property(connection, xid, name)
            except Exception:
                # Unavailable identity hints must never abort a tracking scan.
                hint = WindowIdentityHint(name, IdentityHintStatus.READ_ERROR)
            hints.append(hint)
        return tuple(hints)

    def _initialize(self) -> _X11Connection | None:
        if self._connection is not None:
            return self._connection
        display = Gdk.Display.get_default()
        if not isinstance(display, GdkX11.X11Display):
            return None
        try:
            xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
            xdisplay = ctypes.c_void_p(hash(display.get_xdisplay()))
        except (OSError, TypeError, ValueError):
            return None
        xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        xlib.XInternAtom.restype = ctypes.c_ulong
        xlib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
        ]
        xlib.XGetWindowProperty.restype = ctypes.c_int
        xlib.XFree.argtypes = [ctypes.c_void_p]
        xlib.XFree.restype = ctypes.c_int
        self._connection = _X11Connection(xlib, display, xdisplay)
        return self._connection

    def _atom(self, connection: _X11Connection, name: str) -> int:
        # Do not cache missing atoms: an application may create one later.
        if name not in self._atoms:
            atom = int(
                connection.xlib.XInternAtom(connection.xdisplay, name.encode(), 1)
            )
            if atom:
                self._atoms[name] = atom
            return atom
        return self._atoms[name]

    def _read_property(
        self, connection: _X11Connection, xid: int, name: str
    ) -> WindowIdentityHint:
        atom = self._atom(connection, name)
        if not atom:
            return WindowIdentityHint(name, IdentityHintStatus.ABSENT)
        actual_type = ctypes.c_ulong()
        actual_format = ctypes.c_int()
        item_count = ctypes.c_ulong()
        bytes_after = ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        try:
            connection.display.error_trap_push()
            try:
                status = connection.xlib.XGetWindowProperty(
                    connection.xdisplay,
                    xid,
                    atom,
                    0,
                    MAX_PROPERTY_BYTES // 4,
                    0,
                    0,
                    ctypes.byref(actual_type),
                    ctypes.byref(actual_format),
                    ctypes.byref(item_count),
                    ctypes.byref(bytes_after),
                    ctypes.byref(data),
                )
            finally:
                x_error = connection.display.error_trap_pop()
            if status or x_error:
                return WindowIdentityHint(name, IdentityHintStatus.READ_ERROR)
            if not actual_type.value:
                return WindowIdentityHint(name, IdentityHintStatus.ABSENT)
            if actual_format.value != 8 or actual_type.value not in (
                self._atom(connection, "UTF8_STRING"),
                self._atom(connection, "STRING"),
            ):
                return WindowIdentityHint(name, IdentityHintStatus.MALFORMED)
            if bytes_after.value or item_count.value > MAX_PROPERTY_BYTES:
                return WindowIdentityHint(name, IdentityHintStatus.OVERSIZED)
            if item_count.value and not data:
                return WindowIdentityHint(name, IdentityHintStatus.MALFORMED)
            raw = ctypes.string_at(data, item_count.value) if data else b""
            encoding = (
                "utf-8"
                if actual_type.value == self._atom(connection, "UTF8_STRING")
                else "latin-1"
            )
            try:
                value = raw.rstrip(b"\0").decode(encoding)
            except UnicodeDecodeError:
                return WindowIdentityHint(name, IdentityHintStatus.MALFORMED)
            if "\0" in value:
                return WindowIdentityHint(name, IdentityHintStatus.MALFORMED)
            return WindowIdentityHint(name, IdentityHintStatus.PRESENT, value)
        finally:
            if data:
                connection.xlib.XFree(data)
