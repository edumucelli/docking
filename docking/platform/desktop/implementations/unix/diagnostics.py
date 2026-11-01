"""Read-only X11 compositor diagnostics for the Unix desktop adapter."""

from docking.log import get_logger
from docking.platform.environment import is_x11_backend
from docking.platform.libraries import load_library

log = get_logger("environment")


def compositor_active(*, display: object | None = None) -> bool | None:
    """Return compositor status for X11 backends, or None when unavailable."""
    try:
        import ctypes

        if display is None:
            import gi

            gi.require_version("GdkX11", "3.0")
            from gi.repository import GdkX11

            display = GdkX11.X11Display.get_default()
        if display is None or not is_x11_backend(display=display):
            return None

        xlib = load_library("X11", "libX11.so.6")
        xdisplay = ctypes.c_void_p(hash(display.get_xdisplay()))

        xlib.XDefaultScreen.restype = ctypes.c_int
        xlib.XDefaultScreen.argtypes = [ctypes.c_void_p]
        xlib.XInternAtom.restype = ctypes.c_ulong
        xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        xlib.XGetSelectionOwner.restype = ctypes.c_ulong
        xlib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

        screen_num = xlib.XDefaultScreen(xdisplay)
        atom = xlib.XInternAtom(xdisplay, f"_NET_WM_CM_S{screen_num}".encode(), 0)
        owner = xlib.XGetSelectionOwner(xdisplay, atom)
        return bool(owner != 0)
    except Exception as exc:
        log.warning("failed to check compositor status: %s", exc)
        return None
