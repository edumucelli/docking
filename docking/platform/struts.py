"""X11 strut management -- reserve screen space for the dock.

What are struts?

When a dock or panel sits at a screen edge, other windows (browsers,
terminals, etc.) need to know not to overlap it. The EWMH (Extended
Window Manager Hints) protocol provides the _NET_WM_STRUT_PARTIAL X11
property for exactly this. A window sets this property to declare
"I occupy N pixels along this edge -- please keep other windows out."

The window manager reads these struts and shrinks the available
workspace accordingly. For example, a 53px bottom strut turns a
1920x1080 screen into a 1920x1027 workspace -- maximized windows
stop 53px above the bottom.

    Without struts:                  With bottom strut of 53px:
    ┌────────────────────┐           ┌────────────────────┐
    │                    │           │                    │
    │   browser fills    │           │   browser fills    │
    │   entire screen    │           │   workspace only   │
    │                    │           │                    │
    │                    │           ├────────────────────┤ <- strut boundary
    │  (dock hidden      │           │   dock (53px)      │
    │   behind browser)  │           └────────────────────┘
    └────────────────────┘

The 12-value strut array

_NET_WM_STRUT_PARTIAL is an array of 12 integers:

    [left, right, top, bottom,               <- reserved pixels per edge
     left_start_y,  left_end_y,              <- Y range for left strut
     right_start_y, right_end_y,             <- Y range for right strut
     top_start_x,   top_end_x,              <- X range for top strut
     bottom_start_x, bottom_end_x]          <- X range for bottom strut

The first 4 values say how much space to reserve at each edge.
The remaining 8 values say where along that edge the reservation
applies (important for multi-monitor setups where a strut should
only affect one monitor, not span the entire logical screen).

For a bottom dock on a 1920x1080 monitor at origin:
    bottom       = 53         (53px from screen bottom)
    bottom_start = 0          (starts at left edge of monitor)
    bottom_end   = 1919       (ends at right edge of monitor)
    all others   = 0

Multi-monitor gap

Strut values are relative to the logical screen edge, not the
monitor edge. In a multi-monitor setup the logical screen spans
all monitors. If our monitor doesn't reach the logical screen
edge, we must add the gap:

    Two monitors stacked vertically (monitor A on top, B on bottom):
    ┌─────────────┐ y=0
    │  Monitor A  │
    │  1920x1080  │
    ├─────────────┤ y=1080
    │  Monitor B  │ <- dock here
    │  1920x1080  │
    └─────────────┘ y=2160  (logical screen height)

    For a 53px dock at Monitor B's bottom:
      bottom = 53 + (2160 - 1080 - 1080) = 53 + 0 = 53  <- no gap

    For a 53px dock at Monitor A's bottom:
      bottom = 53 + (2160 - 0 - 1080) = 53 + 1080 = 1133
      The WM measures from logical screen bottom (y=2160), so we
      need 1080px extra to reach Monitor A's bottom edge.

HiDPI scaling

X11 struts operate in physical pixels. On a 2x HiDPI display,
all values must be multiplied by the scale factor. A 53px logical
dock becomes 106 physical pixels in the strut array.

Why ctypes instead of Gdk?

Gdk.property_change() is not available in the PyGObject bindings
shipped with Ubuntu's python3-gi package. We use ctypes to call
Xlib's XChangeProperty directly, which works everywhere.

We also set the older _NET_WM_STRUT (4 values, no start/end pairs)
for compatibility with window managers that don't support the
partial variant.
"""

from __future__ import annotations

import ctypes

from gi.repository import Gdk, GdkX11

from docking.core.position import Position

ATOM_STRUT_PARTIAL = b"_NET_WM_STRUT_PARTIAL"
ATOM_STRUT = b"_NET_WM_STRUT"
ATOM_CARDINAL = b"CARDINAL"


def set_struts(gdk_window: GdkX11.X11Window, struts: list[int]) -> None:
    """Write the raw strut arrays to X11 properties via ctypes/Xlib."""
    xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
    xid = gdk_window.get_xid()
    xdisplay = ctypes.c_void_p(hash(GdkX11.X11Display.get_default().get_xdisplay()))

    xlib.XInternAtom.restype = ctypes.c_ulong
    xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]

    atom_partial = xlib.XInternAtom(xdisplay, ATOM_STRUT_PARTIAL, 0)
    atom_strut = xlib.XInternAtom(xdisplay, ATOM_STRUT, 0)
    xa_cardinal = xlib.XInternAtom(xdisplay, ATOM_CARDINAL, 0)

    xlib.XChangeProperty.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
    ]

    arr12 = (ctypes.c_long * 12)(*struts)
    arr4 = (ctypes.c_long * 4)(*struts[:4])

    xlib.XChangeProperty(
        xdisplay, xid, atom_partial, xa_cardinal, 32, 0, ctypes.byref(arr12), 12
    )
    xlib.XChangeProperty(
        xdisplay, xid, atom_strut, xa_cardinal, 32, 0, ctypes.byref(arr4), 4
    )
    xlib.XFlush(xdisplay)


def compute_struts(
    dock_height: int,
    monitor_x: int,
    monitor_y: int,
    monitor_w: int,
    monitor_h: int,
    screen_w: int,
    screen_h: int,
    scale: int,
    position: Position,
) -> list[int]:
    """Compute the 12-value _NET_WM_STRUT_PARTIAL array.

    All inputs are in logical pixels; outputs are physical pixels
    (multiplied by scale).
    """
    #   Index:  0     1      2    3       4..5          6..7
    #           left  right  top  bottom  left_start/end_y  right_start/end_y
    #   Index:  8..9            10..11
    #           top_start/end_x  bottom_start/end_x
    idx_edge = {
        Position.LEFT: 0,
        Position.RIGHT: 1,
        Position.TOP: 2,
        Position.BOTTOM: 3,
    }
    idx_start = {
        Position.LEFT: 4,
        Position.RIGHT: 6,
        Position.TOP: 8,
        Position.BOTTOM: 10,
    }

    # Gap between monitor edge and logical screen edge (multi-monitor)
    gap = {
        Position.BOTTOM: screen_h - monitor_y - monitor_h,
        Position.TOP: monitor_y,
        Position.LEFT: monitor_x,
        Position.RIGHT: screen_w - monitor_x - monitor_w,
    }

    # Monitor span along the axis parallel to the dock edge
    horizontal = position in (Position.TOP, Position.BOTTOM)
    span_start = int((monitor_x if horizontal else monitor_y) * scale)
    span_end = int(
        ((monitor_x + monitor_w if horizontal else monitor_y + monitor_h) * scale) - 1
    )

    struts = [0] * 12
    struts[idx_edge[position]] = int((dock_height + gap[position]) * scale)
    struts[idx_start[position]] = span_start
    struts[idx_start[position] + 1] = span_end
    return struts


def set_dock_struts(
    gdk_window: GdkX11.X11Window,
    dock_height: int,
    monitor_geom: Gdk.Rectangle,
    screen: Gdk.Screen,
    position: Position = Position.BOTTOM,
) -> None:
    """Compute and set struts for the dock at the given screen edge."""
    struts = compute_struts(
        dock_height=dock_height,
        monitor_x=monitor_geom.x,
        monitor_y=monitor_geom.y,
        monitor_w=monitor_geom.width,
        monitor_h=monitor_geom.height,
        screen_w=screen.get_width(),
        screen_h=screen.get_height(),
        scale=gdk_window.get_scale_factor(),
        position=position,
    )
    set_struts(gdk_window=gdk_window, struts=struts)


def clear_struts(gdk_window: GdkX11.X11Window) -> None:
    """Remove strut reservation by setting all struts to zero."""
    set_struts(gdk_window=gdk_window, struts=[0] * 12)
