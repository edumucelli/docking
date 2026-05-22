# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""X11 pointer barriers used to reinforce edge interaction for the dock.

What a pointer barrier is

A pointer barrier is an invisible line managed by X11 that resists pointer
movement across a chosen edge. It is not a visible widget and it is not the
dock window itself. Conceptually it is:

    "treat this edge like a wall"

for pointer motion.

Why the dock cares

The dock already has an input trigger strip when hidden, but edge interaction is
not always equally reliable across devices and environments. Pointer barriers
provide a lower-level reinforcement for edge behavior, especially when reveal is
supposed to happen right at a monitor boundary.

Typical reasons barriers help:

1. tablet or unusual input devices may not generate the expected dock-enter flow
2. some compositor/edge combinations are less reliable at the physical pixel edge
3. floating dock themes still want strong "hit the edge to reveal" behavior

ASCII sketch:

    screen content
    +-----------------------------------+
    |                                   |
    |                                   |
    |                                   |
    +===================================+  <- pointer barrier line
                                         \\
                                          \\ hidden dock trigger lives here

The barrier does not replace the dock trigger; it reinforces the edge.

Why this module is platform-specific

Pointer barriers are X11/XInput/XFixes concepts. They are not GTK concepts.
That is why this functionality lives under `platform/` rather than in the UI
modules that own hover or autohide policy.

Requirements and graceful fallback

Barriers require:

- XFixes support for pointer barrier creation
- sufficiently new XInput support

If those pieces are unavailable, the dock should still function normally. It
simply falls back to its regular input-region based reveal behavior.

So the contract here is:

    barriers available
      -> create/update barrier along dock edge

    barriers unavailable
      -> do nothing, keep rest of dock working

That graceful fallback is essential because barrier support depends on the
actual runtime X11 stack.

What this module owns

This module owns:

- runtime capability detection,
- creating the current barrier,
- destroying the current barrier,
- translating dock edge choice into a monitor-edge line segment.

It does not own:

- reveal policy,
- pointer pressure interpretation,
- autohide state,
- monitor selection.

Those decisions are handled by placement and autohide layers. This module only
provides the platform primitive.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from typing import TYPE_CHECKING

from docking.core.position import Position
from docking.log import get_logger

if TYPE_CHECKING:
    from gi.repository import GdkX11

log = get_logger(name="barriers")


# X11 / XInput2 constants used to decode barrier events.
# See /usr/include/X11/X.h and X11/extensions/XInput2.h.
_X_GENERIC_EVENT = 35
_XI_BARRIER_HIT = 25
_XI_BARRIER_LEAVE = 26
_XI_ALL_MASTER_DEVICES = 1
_XI_BARRIER_POINTER_RELEASED = 1
_GDK_FILTER_CONTINUE = 0  # Gdk.FilterReturn.CONTINUE

# Per-event accumulation cap. A single fast cursor jab can carry a large dx/dy;
# clamping prevents one event from blowing through the pressure threshold and
# matches Plank's empirically-tuned behaviour.
_PER_EVENT_PRESSURE_CAP = 15.0

# Default pressure threshold (accumulated motion across barrier events). Plank
# converged on 50 after two rounds of tuning (0.11.127, 0.11.157).
DEFAULT_PRESSURE_THRESHOLD = 50


class _XGenericEventCookie(ctypes.Structure):
    """Mirrors X11's ``XGenericEventCookie`` struct."""

    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("extension", ctypes.c_int),
        ("evtype", ctypes.c_int),
        ("cookie", ctypes.c_uint),
        ("data", ctypes.c_void_p),
    ]


class _XIBarrierEvent(ctypes.Structure):
    """Mirrors X11's ``XIBarrierEvent`` (XInput2)."""

    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("extension", ctypes.c_int),
        ("evtype", ctypes.c_int),
        ("time", ctypes.c_ulong),
        ("deviceid", ctypes.c_int),
        ("sourceid", ctypes.c_int),
        ("event", ctypes.c_ulong),
        ("root", ctypes.c_ulong),
        ("root_x", ctypes.c_double),
        ("root_y", ctypes.c_double),
        ("dx", ctypes.c_double),
        ("dy", ctypes.c_double),
        ("dtime", ctypes.c_int),
        ("flags", ctypes.c_int),
        ("barrier", ctypes.c_ulong),
        ("eventid", ctypes.c_uint),
    ]


class _XIEventMask(ctypes.Structure):
    """Mirrors X11's ``XIEventMask`` (XInput2 event selection)."""

    _fields_ = [
        ("deviceid", ctypes.c_int),
        ("mask_len", ctypes.c_int),
        ("mask", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _xi_set_event_mask(byte_index: int) -> int:
    """Return a XI event-mask byte with the given XI event bit set."""
    return 1 << (byte_index & 7)


def _load_libs() -> tuple[ctypes.CDLL, ctypes.CDLL, ctypes.CDLL] | None:
    """Load Xlib, XFixes, and XInput2 shared libraries."""
    try:
        xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
        xfixes = ctypes.cdll.LoadLibrary("libXfixes.so.3")
        xi = ctypes.cdll.LoadLibrary("libXi.so.6")
        return xlib, xfixes, xi
    except OSError as e:
        log.debug("barrier libs unavailable: %s", e)
        return None


def _load_gdk_lib() -> ctypes.CDLL | None:
    """Load libgdk-3 so we can register an X event filter (PyGI does not expose it)."""
    for name in ("libgdk-3.so.0", "libgdk-3.so"):
        try:
            return ctypes.cdll.LoadLibrary(name)
        except OSError:
            continue
    log.debug("libgdk-3 unavailable; pressure-reveal disabled")
    return None


_GDK_FILTER_FUNC = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)


class PointerBarrier:
    """Manages an X11 pointer barrier at a screen edge.

    The barrier spans the full monitor edge where the dock sits.
    On compositors/devices where the dock's thin trigger strip may not
    receive enter events, the barrier ensures the pointer stays at the
    edge long enough for the next pointer poll to detect it.

    Usage:
        barrier = PointerBarrier()
        if barrier.initialize(gdk_display):
            barrier.update(position, mon_x, mon_y, mon_w, mon_h)
        # When dock repositions:
        barrier.update(...)
        # On shutdown:
        barrier.destroy()
    """

    def __init__(self) -> None:
        self._barrier_id: int = 0
        self._xdisplay: ctypes.c_void_p | None = None
        self._libs: tuple[ctypes.CDLL, ctypes.CDLL, ctypes.CDLL] | None = None
        self._supported: bool = False
        # Pressure accumulator state.
        self._xi_opcode: int = 0
        self._position: Position = Position.BOTTOM
        self._pressure_threshold: float = float(DEFAULT_PRESSURE_THRESHOLD)
        self._pressure_callback: Callable[[], None] | None = None
        self._accumulated_pressure: float = 0.0
        self._gdk_display = None  # GdkX11.X11Display when filter installed
        self._filter_installed: bool = False
        self._gdk_lib: ctypes.CDLL | None = None
        # CFUNCTYPE objects must be kept alive while libgdk holds a pointer.
        self._gdk_filter_ref: object | None = None

    @property
    def supported(self) -> bool:
        return self._supported

    def set_pressure_handler(
        self,
        *,
        callback: Callable[[], None] | None,
        threshold: int,
    ) -> None:
        """Register a callback fired when pressure exceeds ``threshold``.

        Pass ``callback=None`` to disable pressure-based reveal; events are
        still received (so the barrier blocks) but never trigger a reveal,
        leaving the normal trigger-strip enter path in charge.
        """
        self._pressure_callback = callback
        self._pressure_threshold = float(max(1, threshold))
        self._accumulated_pressure = 0.0
        # Lazily install the GDK X event filter the first time pressure is
        # actually wanted: until then there's no reason to pay the ctypes
        # filter overhead.
        if callback is not None:
            self._install_gdk_filter()

    def initialize(self, gdk_display: GdkX11.X11Display) -> bool:
        """Check XInput 2.3+ support. Returns True if barriers work."""
        self._libs = _load_libs()
        if self._libs is None:
            return False

        xlib, _, xi = self._libs
        self._xdisplay = ctypes.c_void_p(hash(gdk_display.get_xdisplay()))

        # Check XInput extension is present
        xlib.XQueryExtension.restype = ctypes.c_int
        xlib.XQueryExtension.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        opcode = ctypes.c_int()
        evt = ctypes.c_int()
        err = ctypes.c_int()
        if not xlib.XQueryExtension(
            self._xdisplay,
            b"XInputExtension",
            ctypes.byref(opcode),
            ctypes.byref(evt),
            ctypes.byref(err),
        ):
            log.debug("XInput extension not available")
            return False
        self._xi_opcode = opcode.value
        self._gdk_display = gdk_display

        # Verify XInput >= 2.3
        xi.XIQueryVersion.restype = ctypes.c_int
        xi.XIQueryVersion.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        major = ctypes.c_int(2)
        minor = ctypes.c_int(3)
        if (
            xi.XIQueryVersion(self._xdisplay, ctypes.byref(major), ctypes.byref(minor))
            != 0
        ):
            log.debug("XInput2 query failed")
            return False
        if major.value < 2 or (major.value == 2 and minor.value < 3):
            log.debug(
                "XInput %d.%d insufficient (need 2.3+)",
                major.value,
                minor.value,
            )
            return False

        log.info(
            "barriers supported (XInput %d.%d)",
            major.value,
            minor.value,
        )
        self._supported = True
        return True

    def update(
        self,
        position: Position,
        monitor_x: int,
        monitor_y: int,
        monitor_w: int,
        monitor_h: int,
        scale: int = 1,
    ) -> None:
        """Create or recreate the barrier at the monitor's dock edge.

        Monitor coordinates are in GDK logical pixels. XFixes operates on the
        X11 root window in physical pixels, so the scale factor is applied
        before creating the barrier.
        """
        if not self._supported or self._libs is None:
            return

        self.destroy()
        self._position = position
        self._accumulated_pressure = 0.0

        xlib, xfixes, _ = self._libs
        mx, my = monitor_x * scale, monitor_y * scale
        mw, mh = monitor_w * scale, monitor_h * scale

        # Barrier line spans the full dock edge of the monitor
        if position == Position.BOTTOM:
            x1, y1 = mx, my + mh
            x2, y2 = mx + mw, my + mh
        elif position == Position.TOP:
            x1, y1 = mx, my
            x2, y2 = mx + mw, my
        elif position == Position.LEFT:
            x1, y1 = mx, my
            x2, y2 = mx, my + mh
        else:  # RIGHT
            x1, y1 = mx + mw, my
            x2, y2 = mx + mw, my + mh

        xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        root = xlib.XDefaultRootWindow(self._xdisplay)

        xfixes.XFixesCreatePointerBarrier.restype = ctypes.c_ulong
        xfixes.XFixesCreatePointerBarrier.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        self._barrier_id = xfixes.XFixesCreatePointerBarrier(
            self._xdisplay,
            root,
            x1,
            y1,
            x2,
            y2,
            0,  # directions = 0 (block in all directions)
            0,
            None,  # num_devices, devices
        )

        if self._barrier_id:
            log.debug(
                "barrier created: (%d,%d)-(%d,%d) id=%d",
                x1,
                y1,
                x2,
                y2,
                self._barrier_id,
            )
            self._subscribe_to_barrier_events(root=root)
        else:
            log.warning("failed to create pointer barrier")

    def destroy(self) -> None:
        """Remove the current barrier if any."""
        if self._barrier_id and self._libs:
            _, xfixes, _ = self._libs
            xfixes.XFixesDestroyPointerBarrier.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
            ]
            xfixes.XFixesDestroyPointerBarrier(self._xdisplay, self._barrier_id)
            log.debug("barrier destroyed: id=%d", self._barrier_id)
            self._barrier_id = 0
        self._accumulated_pressure = 0.0

    def shutdown(self) -> None:
        """Tear down the barrier and remove the global event filter."""
        self.destroy()
        if self._filter_installed and self._gdk_lib is not None:
            self._gdk_lib.gdk_window_remove_filter.argtypes = [
                ctypes.c_void_p,
                _GDK_FILTER_FUNC,
                ctypes.c_void_p,
            ]
            self._gdk_lib.gdk_window_remove_filter.restype = None
            try:
                self._gdk_lib.gdk_window_remove_filter(None, self._gdk_filter_ref, None)
            except Exception as exc:
                log.warning("failed to remove barrier filter: %s", exc)
            self._filter_installed = False
            self._gdk_filter_ref = None

    def _subscribe_to_barrier_events(self, *, root: int) -> None:
        """Register for XI_BarrierHit/Leave and install the GDK filter."""
        if self._libs is None:
            return
        _, _, xi = self._libs

        # Build XIEventMask with BarrierHit + BarrierLeave bits set. The mask
        # needs to be long enough to cover the highest XI event id we care
        # about; one byte per 8 events is the X11 convention.
        mask_len = (max(_XI_BARRIER_HIT, _XI_BARRIER_LEAVE) // 8) + 1
        mask = (ctypes.c_ubyte * mask_len)()
        mask[_XI_BARRIER_HIT // 8] |= _xi_set_event_mask(_XI_BARRIER_HIT)
        mask[_XI_BARRIER_LEAVE // 8] |= _xi_set_event_mask(_XI_BARRIER_LEAVE)

        event_mask = _XIEventMask(
            deviceid=_XI_ALL_MASTER_DEVICES,
            mask_len=mask_len,
            mask=ctypes.cast(mask, ctypes.POINTER(ctypes.c_ubyte)),
        )

        xi.XISelectEvents.restype = ctypes.c_int
        xi.XISelectEvents.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(_XIEventMask),
            ctypes.c_int,
        ]
        status = xi.XISelectEvents(self._xdisplay, root, ctypes.byref(event_mask), 1)
        if status != 0:
            log.warning("XISelectEvents failed for barrier events: %d", status)

    def _install_gdk_filter(self) -> None:
        """Install a GDK X event filter via ctypes (PyGI does not expose it).

        ``gdk_window_add_filter`` takes a callback receiving the raw
        ``XEvent*``. The callback signature is not introspectable through
        GObject Introspection (``GdkXEvent`` is opaque), so PyGObject hides
        the function. We call libgdk-3 directly via ctypes.
        """
        if self._filter_installed:
            return
        self._gdk_lib = _load_gdk_lib()
        if self._gdk_lib is None:
            return

        gdk_lib = self._gdk_lib
        gdk_lib.gdk_window_add_filter.argtypes = [
            ctypes.c_void_p,
            _GDK_FILTER_FUNC,
            ctypes.c_void_p,
        ]
        gdk_lib.gdk_window_add_filter.restype = None
        self._gdk_filter_ref = _GDK_FILTER_FUNC(self._gdk_filter_callback)
        # window=NULL means "all toplevel windows" -- exactly Plank's pattern.
        gdk_lib.gdk_window_add_filter(None, self._gdk_filter_ref, None)
        self._filter_installed = True

    def _gdk_filter_callback(self, xevent_ptr, _gdk_event_ptr, _user_data):
        """C-callable filter; delegates to the safe Python handler."""
        try:
            return self._handle_x_event(xevent_ptr)
        except Exception as exc:
            log.warning("barrier filter callback raised: %s", exc)
            return _GDK_FILTER_CONTINUE

    def _handle_x_event(self, xevent_ptr_int) -> int:
        """Read the XEvent at ``xevent_ptr_int``; consume barrier events for our id."""
        if self._libs is None or self._barrier_id == 0 or not xevent_ptr_int:
            return _GDK_FILTER_CONTINUE

        cookie_ptr = ctypes.cast(
            ctypes.c_void_p(xevent_ptr_int),
            ctypes.POINTER(_XGenericEventCookie),
        )
        cookie = cookie_ptr.contents
        if cookie.type != _X_GENERIC_EVENT:
            return _GDK_FILTER_CONTINUE
        if cookie.extension != self._xi_opcode:
            return _GDK_FILTER_CONTINUE
        if cookie.evtype not in (_XI_BARRIER_HIT, _XI_BARRIER_LEAVE):
            return _GDK_FILTER_CONTINUE

        xlib, _, _ = self._libs
        xlib.XGetEventData.restype = ctypes.c_int
        xlib.XGetEventData.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_XGenericEventCookie),
        ]
        xlib.XFreeEventData.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_XGenericEventCookie),
        ]
        if not xlib.XGetEventData(self._xdisplay, cookie_ptr):
            return _GDK_FILTER_CONTINUE
        try:
            barrier_event = ctypes.cast(
                cookie.data, ctypes.POINTER(_XIBarrierEvent)
            ).contents
            if barrier_event.barrier != self._barrier_id:
                return _GDK_FILTER_CONTINUE
            if cookie.evtype == _XI_BARRIER_LEAVE:
                self._accumulated_pressure = 0.0
                return _GDK_FILTER_CONTINUE
            self._handle_barrier_hit(
                dx=float(barrier_event.dx), dy=float(barrier_event.dy)
            )
        finally:
            xlib.XFreeEventData(self._xdisplay, cookie_ptr)
        return _GDK_FILTER_CONTINUE

    def _handle_barrier_hit(self, *, dx: float, dy: float) -> None:
        """Accumulate barrier pressure; fire the callback past threshold."""
        if self._pressure_callback is None:
            return

        # The barrier event reports motion the barrier resisted. Split that
        # motion into a perpendicular component (into the barrier, what we
        # want to count as "pressure") and a parallel component ("slide").
        # Only accumulate when the user is genuinely pushing into the edge,
        # not skating along it.
        if self._position in (Position.BOTTOM, Position.TOP):
            distance = abs(dy)
            slide = abs(dx)
        else:
            distance = abs(dx)
            slide = abs(dy)

        if slide >= distance:
            return

        # Per-event clamp: a single fast jab shouldn't blow past the
        # threshold instantly.
        delta = min(_PER_EVENT_PRESSURE_CAP, distance)
        self._accumulated_pressure += delta

        if self._accumulated_pressure >= self._pressure_threshold:
            log.debug(
                "barrier pressure threshold reached: %.1f >= %.1f",
                self._accumulated_pressure,
                self._pressure_threshold,
            )
            self._accumulated_pressure = 0.0
            try:
                self._pressure_callback()
            except Exception as exc:
                log.warning("pressure callback raised: %s", exc)
