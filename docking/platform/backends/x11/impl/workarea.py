# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""External X11 panel discovery and monitor workarea composition.

GDK's monitor workarea includes this dock's own strut after it is published.
Using that value for later placement feeds our reservation back into our
position. This module instead reads every other mapped client's EWMH strut,
excludes our XID, and derives a stable external workarea.
"""

from __future__ import annotations

import ctypes
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, GLib

from docking.log import get_logger
from docking.platform.backends.base import MonitorSnapshot, Rect

log = get_logger(name="x11-workarea")

_ATOM_CARDINAL = b"CARDINAL"
_ATOM_WINDOW = b"WINDOW"
_ATOM_CLIENT_LIST = b"_NET_CLIENT_LIST"
_ATOM_CLIENT_LIST_STACKING = b"_NET_CLIENT_LIST_STACKING"
_ATOM_STRUT = b"_NET_WM_STRUT"
_ATOM_STRUT_PARTIAL = b"_NET_WM_STRUT_PARTIAL"
_ATOM_WORKAREA = b"_NET_WORKAREA"

_PROPERTY_NOTIFY = 28
_MAP_NOTIFY = 19
_UNMAP_NOTIFY = 18
_DESTROY_NOTIFY = 17
_CONFIGURE_NOTIFY = 22
_IS_VIEWABLE = 2
_ANY_PROPERTY_TYPE = 0
_GDK_FILTER_CONTINUE = 0


@dataclass(frozen=True)
class StrutReservation:
    """One mapped external X11 client's normalized 12-value strut."""

    window_id: int
    values: tuple[int, ...]


@dataclass(frozen=True)
class ExternalStrutSnapshot:
    """Stable external reservation state used for change detection."""

    available: bool
    screen_width: int
    screen_height: int
    reservations: tuple[StrutReservation, ...]


def normalize_strut(
    values: Sequence[int], *, screen_width: int, screen_height: int
) -> tuple[int, ...] | None:
    """Normalize partial or legacy EWMH struts to 12 non-negative values."""
    if len(values) >= 12:
        normalized = tuple(max(0, int(value)) for value in values[:12])
    elif len(values) >= 4:
        left, right, top, bottom = (max(0, int(value)) for value in values[:4])
        normalized = (
            left,
            right,
            top,
            bottom,
            0,
            max(0, screen_height - 1),
            0,
            max(0, screen_height - 1),
            0,
            max(0, screen_width - 1),
            0,
            max(0, screen_width - 1),
        )
    else:
        return None
    return normalized


def compute_external_workarea(
    *,
    monitor: Rect,
    screen_width: int,
    screen_height: int,
    scale: int,
    reservations: Sequence[StrutReservation],
) -> Rect:
    """Subtract intersecting external struts from one logical monitor."""
    scale = max(1, int(scale))
    monitor_left = monitor.x * scale
    monitor_top = monitor.y * scale
    monitor_right = monitor.right * scale
    monitor_bottom = monitor.bottom * scale

    left = monitor_left
    top = monitor_top
    right = monitor_right
    bottom = monitor_bottom

    for reservation in reservations:
        values = reservation.values
        if len(values) < 12:
            continue
        if values[0] and _inclusive_spans_overlap(
            values[4], values[5], monitor_top, monitor_bottom - 1
        ):
            left = max(left, min(screen_width, values[0]))
        if values[1] and _inclusive_spans_overlap(
            values[6], values[7], monitor_top, monitor_bottom - 1
        ):
            right = min(right, max(0, screen_width - values[1]))
        if values[2] and _inclusive_spans_overlap(
            values[8], values[9], monitor_left, monitor_right - 1
        ):
            top = max(top, min(screen_height, values[2]))
        if values[3] and _inclusive_spans_overlap(
            values[10], values[11], monitor_left, monitor_right - 1
        ):
            bottom = min(bottom, max(0, screen_height - values[3]))

    logical_left = max(monitor.x, math.ceil(left / scale))
    logical_top = max(monitor.y, math.ceil(top / scale))
    logical_right = min(monitor.right, math.floor(right / scale))
    logical_bottom = min(monitor.bottom, math.floor(bottom / scale))
    return Rect(
        x=logical_left,
        y=logical_top,
        width=max(0, logical_right - logical_left),
        height=max(0, logical_bottom - logical_top),
    )


def _inclusive_spans_overlap(
    first_start: int, first_end: int, second_start: int, second_end: int
) -> bool:
    return first_start <= second_end and first_end >= second_start


class _XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("border_width", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p),
        ("root", ctypes.c_ulong),
        ("class_", ctypes.c_int),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int),
        ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long),
        ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("screen", ctypes.c_void_p),
    ]


class _XPropertyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("atom", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("state", ctypes.c_int),
    ]


class _XStructureEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("event", ctypes.c_ulong),
        ("window", ctypes.c_ulong),
    ]


_GDK_FILTER_FUNC = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)


class ExternalWorkareaTracker:
    """Track mapped foreign EWMH struts and notify on effective changes."""

    def __init__(self) -> None:
        self._xlib: ctypes.CDLL | None = None
        self._gdk_lib: ctypes.CDLL | None = None
        self._xdisplay: ctypes.c_void_p | None = None
        self._gdk_display: object | None = None
        self._root: int = 0
        self._own_xid: int = 0
        self._atoms: dict[bytes, int] = {}
        self._tracked_windows: dict[int, object] = {}
        self._snapshot = ExternalStrutSnapshot(False, 0, 0, ())
        self._change_handler: Callable[[], None] | None = None
        self._refresh_source: int = 0
        self._filter_installed = False
        self._filter_ref: object | None = None

    def initialize(
        self,
        *,
        gdk_display: GdkX11.X11Display,
        own_xid: int,
    ) -> bool:
        """Start tracking reservations for an X11 display."""
        self.stop()
        try:
            self._xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
            self._gdk_lib = _load_gdk_lib()
        except OSError as error:
            log.debug("external workarea unavailable: %s", error)
            return False
        if self._gdk_lib is None:
            return False

        self._gdk_display = gdk_display
        self._xdisplay = ctypes.c_void_p(hash(gdk_display.get_xdisplay()))
        self._own_xid = int(own_xid)
        self._configure_xlib()
        screen_number = self._xlib.XDefaultScreen(self._xdisplay)
        self._root = int(self._xlib.XRootWindow(self._xdisplay, screen_number))
        self._atoms = {
            name: int(self._xlib.XInternAtom(self._xdisplay, name, 0))
            for name in (
                _ATOM_CARDINAL,
                _ATOM_WINDOW,
                _ATOM_CLIENT_LIST,
                _ATOM_CLIENT_LIST_STACKING,
                _ATOM_STRUT,
                _ATOM_STRUT_PARTIAL,
                _ATOM_WORKAREA,
            )
        }
        self._select_root_events()
        self._snapshot = self._read_snapshot()
        self._install_filter()
        log.debug(
            "external workarea initialized: clients=%d reservations=%d",
            len(self._tracked_windows),
            len(self._snapshot.reservations),
        )
        return True

    def stop(self) -> None:
        """Remove event filter and pending refresh."""
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = 0
        if self._filter_installed and self._gdk_lib and self._filter_ref:
            try:
                self._gdk_lib.gdk_window_remove_filter(None, self._filter_ref, None)
            except Exception as error:
                log.warning("failed to remove external workarea filter: %s", error)
        self._filter_installed = False
        self._filter_ref = None
        self._tracked_windows.clear()
        self._snapshot = ExternalStrutSnapshot(False, 0, 0, ())
        self._xlib = None
        self._gdk_lib = None
        self._xdisplay = None
        self._gdk_display = None
        self._root = 0
        self._atoms = {}

    def set_change_handler(self, callback: Callable[[], None] | None) -> None:
        self._change_handler = callback

    def workarea_for(self, monitor: MonitorSnapshot) -> Rect | None:
        if not self._snapshot.available:
            return None
        return compute_external_workarea(
            monitor=monitor.geometry,
            screen_width=self._snapshot.screen_width,
            screen_height=self._snapshot.screen_height,
            scale=monitor.scale,
            reservations=self._snapshot.reservations,
        )

    def refresh(self) -> None:
        """Synchronously refresh after monitor or root-screen reconfiguration."""
        if self._xlib is None or self._xdisplay is None:
            return
        self._snapshot = self._read_snapshot()

    def _configure_xlib(self) -> None:
        assert self._xlib is not None
        self._xlib.XDefaultScreen.argtypes = [ctypes.c_void_p]
        self._xlib.XDefaultScreen.restype = ctypes.c_int
        self._xlib.XRootWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._xlib.XRootWindow.restype = ctypes.c_ulong
        self._xlib.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._xlib.XDisplayWidth.restype = ctypes.c_int
        self._xlib.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._xlib.XDisplayHeight.restype = ctypes.c_int
        self._xlib.XInternAtom.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        self._xlib.XInternAtom.restype = ctypes.c_ulong
        self._xlib.XGetWindowProperty.argtypes = [
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
        self._xlib.XGetWindowProperty.restype = ctypes.c_int
        self._xlib.XGetWindowAttributes.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(_XWindowAttributes),
        ]
        self._xlib.XGetWindowAttributes.restype = ctypes.c_int
        self._xlib.XFree.argtypes = [ctypes.c_void_p]

    def _read_snapshot(self) -> ExternalStrutSnapshot:
        assert self._xlib is not None and self._xdisplay is not None
        screen_number = self._xlib.XDefaultScreen(self._xdisplay)
        screen_width = int(self._xlib.XDisplayWidth(self._xdisplay, screen_number))
        screen_height = int(self._xlib.XDisplayHeight(self._xdisplay, screen_number))
        clients = self._read_property(
            self._root,
            self._atoms[_ATOM_CLIENT_LIST_STACKING],
            expected_type=self._atoms[_ATOM_WINDOW],
        )
        if clients is None:
            clients = self._read_property(
                self._root,
                self._atoms[_ATOM_CLIENT_LIST],
                expected_type=self._atoms[_ATOM_WINDOW],
            )
        if clients is None:
            self._tracked_windows.clear()
            return ExternalStrutSnapshot(False, screen_width, screen_height, ())

        reservations: list[StrutReservation] = []
        live_clients = {int(xid) for xid in clients if int(xid) != self._own_xid}
        strut_clients: set[int] = set()
        for xid in sorted(live_clients):
            values = self._read_property(
                xid,
                self._atoms[_ATOM_STRUT_PARTIAL],
                expected_type=self._atoms[_ATOM_CARDINAL],
            )
            if values is None or len(values) < 12:
                values = self._read_property(
                    xid,
                    self._atoms[_ATOM_STRUT],
                    expected_type=self._atoms[_ATOM_CARDINAL],
                )
            if values is None:
                continue
            normalized = normalize_strut(
                values,
                screen_width=screen_width,
                screen_height=screen_height,
            )
            if normalized is None or not any(normalized[:4]):
                continue
            if not self._is_mapped(xid):
                continue
            strut_clients.add(xid)
            reservations.append(StrutReservation(xid, normalized))
        self._subscribe_clients(strut_clients)
        return ExternalStrutSnapshot(
            True, screen_width, screen_height, tuple(reservations)
        )

    def _read_property(
        self, xid: int, atom: int, *, expected_type: int
    ) -> list[int] | None:
        assert self._xlib is not None and self._xdisplay is not None
        assert self._gdk_display is not None
        actual_type = ctypes.c_ulong()
        actual_format = ctypes.c_int()
        item_count = ctypes.c_ulong()
        bytes_after = ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        self._gdk_display.error_trap_push()
        try:
            status = self._xlib.XGetWindowProperty(
                self._xdisplay,
                xid,
                atom,
                0,
                1024,
                0,
                _ANY_PROPERTY_TYPE,
                ctypes.byref(actual_type),
                ctypes.byref(actual_format),
                ctypes.byref(item_count),
                ctypes.byref(bytes_after),
                ctypes.byref(data),
            )
        finally:
            x_error = self._gdk_display.error_trap_pop()
        if (
            x_error != 0
            or status != 0
            or actual_type.value != expected_type
            or actual_format.value != 32
        ):
            if data:
                self._xlib.XFree(data)
            return None
        try:
            values = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
            return [int(values[index]) for index in range(item_count.value)]
        finally:
            if data:
                self._xlib.XFree(data)

    def _is_mapped(self, xid: int) -> bool:
        assert self._xlib is not None and self._xdisplay is not None
        assert self._gdk_display is not None
        attributes = _XWindowAttributes()
        self._gdk_display.error_trap_push()
        try:
            status = self._xlib.XGetWindowAttributes(
                self._xdisplay, xid, ctypes.byref(attributes)
            )
        finally:
            x_error = self._gdk_display.error_trap_pop()
        return bool(x_error == 0 and status and attributes.map_state == _IS_VIEWABLE)

    def _select_root_events(self) -> None:
        if self._gdk_display is None:
            return
        root_window = self._gdk_display.get_default_screen().get_root_window()
        if root_window is None:
            return
        root_window.set_events(
            root_window.get_events()
            | Gdk.EventMask.PROPERTY_CHANGE_MASK
            | Gdk.EventMask.SUBSTRUCTURE_MASK
        )

    def _subscribe_clients(self, clients: set[int]) -> None:
        if self._gdk_display is None:
            return
        for xid in set(self._tracked_windows) - clients:
            self._tracked_windows.pop(xid, None)
        for xid in clients - set(self._tracked_windows):
            self._gdk_display.error_trap_push()
            try:
                window = GdkX11.X11Window.foreign_new_for_display(
                    self._gdk_display, xid
                )
                if window is None:
                    continue
                window.set_events(
                    window.get_events()
                    | Gdk.EventMask.PROPERTY_CHANGE_MASK
                    | Gdk.EventMask.STRUCTURE_MASK
                )
            except (AttributeError, TypeError):
                window = None
            finally:
                x_error = self._gdk_display.error_trap_pop()
            if x_error == 0 and window is not None:
                self._tracked_windows[xid] = window

    def _install_filter(self) -> None:
        assert self._gdk_lib is not None
        self._gdk_lib.gdk_window_add_filter.argtypes = [
            ctypes.c_void_p,
            _GDK_FILTER_FUNC,
            ctypes.c_void_p,
        ]
        self._gdk_lib.gdk_window_add_filter.restype = None
        self._gdk_lib.gdk_window_remove_filter.argtypes = [
            ctypes.c_void_p,
            _GDK_FILTER_FUNC,
            ctypes.c_void_p,
        ]
        self._gdk_lib.gdk_window_remove_filter.restype = None
        self._filter_ref = _GDK_FILTER_FUNC(self._gdk_filter_callback)
        self._gdk_lib.gdk_window_add_filter(None, self._filter_ref, None)
        self._filter_installed = True

    def _gdk_filter_callback(
        self, xevent_ptr: int, event_ptr: int, data_ptr: int
    ) -> int:
        try:
            return self._event_filter(xevent_ptr, event_ptr, data_ptr)
        except Exception as error:
            log.warning("external workarea filter callback raised: %s", error)
            return _GDK_FILTER_CONTINUE

    def _event_filter(self, xevent_ptr: int, _event_ptr: int, _data_ptr: int) -> int:
        if not xevent_ptr:
            return _GDK_FILTER_CONTINUE
        event_type = ctypes.cast(
            xevent_ptr, ctypes.POINTER(ctypes.c_int)
        ).contents.value
        if event_type == _PROPERTY_NOTIFY:
            event = ctypes.cast(xevent_ptr, ctypes.POINTER(_XPropertyEvent)).contents
            root_atoms = {
                self._atoms.get(_ATOM_CLIENT_LIST, 0),
                self._atoms.get(_ATOM_CLIENT_LIST_STACKING, 0),
                self._atoms.get(_ATOM_WORKAREA, 0),
            }
            strut_atoms = {
                self._atoms.get(_ATOM_STRUT, 0),
                self._atoms.get(_ATOM_STRUT_PARTIAL, 0),
            }
            if (event.window == self._root and event.atom in root_atoms) or (
                event.window in self._tracked_windows and event.atom in strut_atoms
            ):
                self._schedule_refresh()
        elif event_type in {_MAP_NOTIFY, _UNMAP_NOTIFY, _DESTROY_NOTIFY}:
            event = ctypes.cast(xevent_ptr, ctypes.POINTER(_XStructureEvent)).contents
            if event.event == self._root or event.window in self._tracked_windows:
                self._schedule_refresh()
        elif event_type == _CONFIGURE_NOTIFY:
            event = ctypes.cast(xevent_ptr, ctypes.POINTER(_XStructureEvent)).contents
            if event.window in self._tracked_windows:
                self._schedule_refresh()
        return _GDK_FILTER_CONTINUE

    def _schedule_refresh(self) -> None:
        if not self._refresh_source:
            self._refresh_source = GLib.idle_add(self._apply_refresh)

    def _apply_refresh(self) -> bool:
        self._refresh_source = 0
        old_snapshot = self._snapshot
        self._snapshot = self._read_snapshot()
        if self._snapshot != old_snapshot and callable(self._change_handler):
            self._change_handler()
        return False


def _load_gdk_lib() -> ctypes.CDLL | None:
    for name in ("libgdk-3.so.0", "libgdk-3.so"):
        try:
            return ctypes.cdll.LoadLibrary(name)
        except OSError:
            continue
    log.debug("libgdk-3 unavailable; external workarea tracking disabled")
    return None
