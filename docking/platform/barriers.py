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
from typing import TYPE_CHECKING

from docking.core.position import Position
from docking.log import get_logger

if TYPE_CHECKING:
    from gi.repository import GdkX11

log = get_logger(name="barriers")


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

    @property
    def supported(self) -> bool:
        return self._supported

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
