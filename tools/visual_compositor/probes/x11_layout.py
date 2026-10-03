"""Arrange only large output windows on the run's private Xvfb display."""

import ctypes
import ctypes.util
import os
from pathlib import Path

lab = Path(os.environ["LAB_DIR"])
os.environ["XAUTHORITY"] = (lab / "outer-authority").read_text().strip()
xlib = ctypes.CDLL(ctypes.util.find_library("X11"))
window = ctypes.c_ulong
integer, unsigned = ctypes.c_int, ctypes.c_uint
pointer = ctypes.c_void_p
xlib.XOpenDisplay.argtypes, xlib.XOpenDisplay.restype = [ctypes.c_char_p], pointer
xlib.XDefaultRootWindow.argtypes, xlib.XDefaultRootWindow.restype = [pointer], window
xlib.XQueryTree.argtypes = [
    pointer,
    window,
    ctypes.POINTER(window),
    ctypes.POINTER(window),
    ctypes.POINTER(ctypes.POINTER(window)),
    ctypes.POINTER(unsigned),
]
xlib.XGetGeometry.argtypes = [
    pointer,
    window,
    ctypes.POINTER(window),
    ctypes.POINTER(integer),
    ctypes.POINTER(integer),
    ctypes.POINTER(unsigned),
    ctypes.POINTER(unsigned),
    ctypes.POINTER(unsigned),
    ctypes.POINTER(unsigned),
]
xlib.XMoveWindow.argtypes = [pointer, window, integer, integer]
xlib.XSync.argtypes = [pointer, integer]
xlib.XCloseDisplay.argtypes = [pointer]
xlib.XFree.argtypes = [pointer]
display = xlib.XOpenDisplay((lab / "outer-display").read_text().strip().encode())
if not display:
    raise RuntimeError("private Xvfb display unavailable")
children = ctypes.POINTER(window)()
root, parent, count = window(), window(), unsigned()
try:
    if not xlib.XQueryTree(
        display,
        xlib.XDefaultRootWindow(display),
        ctypes.byref(root),
        ctypes.byref(parent),
        ctypes.byref(children),
        ctypes.byref(count),
    ):
        raise RuntimeError("cannot inspect private parent windows")
    outputs = []
    for index in range(count.value):
        x, y = integer(), integer()
        width, height, border, depth = unsigned(), unsigned(), unsigned(), unsigned()
        if (
            xlib.XGetGeometry(
                display,
                children[index],
                ctypes.byref(root),
                ctypes.byref(x),
                ctypes.byref(y),
                ctypes.byref(width),
                ctypes.byref(height),
                ctypes.byref(border),
                ctypes.byref(depth),
            )
            and width.value >= 100
            and height.value >= 100
        ):
            outputs.append((children[index], width.value))
    if len(outputs) != int(os.environ.get("LAB_OUTPUTS", "1")):
        raise RuntimeError(f"unexpected large windows on private Xvfb: {outputs}")
    x = 0
    for identifier, width in sorted(outputs):
        xlib.XMoveWindow(display, identifier, x, 0)
        x += width
    xlib.XSync(display, False)
finally:
    if children:
        xlib.XFree(children)
    xlib.XCloseDisplay(display)
