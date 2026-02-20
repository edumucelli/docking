"""X11 strut management — reserve screen space for the dock."""

from __future__ import annotations

import ctypes

from gi.repository import Gdk, GdkX11


def set_struts(gdk_window: GdkX11.X11Window, struts: list[int]) -> None:
    """Set _NET_WM_STRUT and _NET_WM_STRUT_PARTIAL via ctypes/Xlib."""
    xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
    xid = gdk_window.get_xid()
    xdisplay = ctypes.c_void_p(hash(
        GdkX11.X11Display.get_default().get_xdisplay()
    ))

    xlib.XInternAtom.restype = ctypes.c_ulong
    xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]

    atom_partial = xlib.XInternAtom(xdisplay, b"_NET_WM_STRUT_PARTIAL", 0)
    atom_strut = xlib.XInternAtom(xdisplay, b"_NET_WM_STRUT", 0)
    xa_cardinal = xlib.XInternAtom(xdisplay, b"CARDINAL", 0)

    xlib.XChangeProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_int,
    ]

    arr12 = (ctypes.c_long * 12)(*struts)
    arr4 = (ctypes.c_long * 4)(*struts[:4])

    xlib.XChangeProperty(xdisplay, xid, atom_partial, xa_cardinal, 32, 0, ctypes.byref(arr12), 12)
    xlib.XChangeProperty(xdisplay, xid, atom_strut, xa_cardinal, 32, 0, ctypes.byref(arr4), 4)
    xlib.XFlush(xdisplay)


def set_dock_struts(gdk_window: GdkX11.X11Window, dock_height: int,
                    monitor_geom: Gdk.Rectangle, screen: Gdk.Screen) -> None:
    """Compute and set struts for the dock at the bottom of a monitor."""
    scale = gdk_window.get_scale_factor()
    screen_h = screen.get_height()
    bottom = (dock_height + screen_h - monitor_geom.y - monitor_geom.height) * scale
    bottom_start = monitor_geom.x * scale
    bottom_end = (monitor_geom.x + monitor_geom.width) * scale - 1

    struts = [0, 0, 0, int(bottom), 0, 0, 0, 0, 0, 0, int(bottom_start), int(bottom_end)]
    set_struts(gdk_window, struts)


def clear_struts(gdk_window: GdkX11.X11Window) -> None:
    """Remove strut reservation by setting all struts to zero."""
    set_struts(gdk_window, [0] * 12)
