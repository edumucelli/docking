"""Desktop environment detection and DE-specific tweaks.

Reads XDG_SESSION_DESKTOP, XDG_CURRENT_DESKTOP, and DESKTOP_SESSION
(in that priority order, matching Plank Reloaded's Environment.vala)
to determine the running desktop environment.

DE-specific tweaks applied at startup:

- **Xfce**: Disables xfwm4 dock shadow via xfconf-query. Without this,
  xfwm4 draws a window shadow around the dock that looks wrong for a
  panel-type window.

- **Monitor geometry**: On GNOME/MATE/Cinnamon/Xfce/KDE the dock uses
  full monitor geometry for positioning (these DEs handle panels via
  struts). On unknown DEs, workarea is used as a safer fallback.
"""

from __future__ import annotations

import enum
import os
import shutil
import subprocess

from docking.log import get_logger

log = get_logger(name="environment")


class Desktop(enum.Flag):
    UNKNOWN = 0
    GNOME = enum.auto()
    KDE = enum.auto()
    LXDE = enum.auto()
    MATE = enum.auto()
    XFCE = enum.auto()
    CINNAMON = enum.auto()
    PANTHEON = enum.auto()
    UNITY = enum.auto()
    UBUNTU = enum.auto()

    @property
    def uses_monitor_geometry(self) -> bool:
        """Whether to use full monitor geometry instead of workarea.

        These DEs handle panel space reservation via struts, so the dock
        should position relative to the full monitor and let struts
        carve out workspace.
        """
        known = (
            Desktop.GNOME
            | Desktop.UBUNTU
            | Desktop.MATE
            | Desktop.CINNAMON
            | Desktop.XFCE
            | Desktop.KDE
        )
        return bool(self & known)


_DESKTOP_MAP: dict[str, Desktop] = {
    "gnome": Desktop.GNOME,
    "gnome-xorg": Desktop.GNOME,
    "gnome-classic": Desktop.GNOME,
    "gnome-flashback": Desktop.GNOME,
    "ubuntu": Desktop.UBUNTU,
    "ubuntu-xorg": Desktop.UBUNTU,
    "kde": Desktop.KDE,
    "lxde": Desktop.LXDE,
    "lxqt": Desktop.LXDE,
    "mate": Desktop.MATE,
    "xfce": Desktop.XFCE,
    "xubuntu": Desktop.XFCE,
    "cinnamon": Desktop.CINNAMON,
    "x-cinnamon": Desktop.CINNAMON,
    "pantheon": Desktop.PANTHEON,
    "unity": Desktop.UNITY,
}


def _parse_desktop(value: str) -> Desktop:
    """Parse a desktop string, handling semicolon-separated values."""
    result = Desktop.UNKNOWN
    for part in value.split(";"):
        part = part.strip().lower()
        if part:
            result |= _DESKTOP_MAP.get(part, Desktop.UNKNOWN)
    return result


def detect_desktop() -> Desktop:
    """Detect the desktop environment from XDG environment variables.

    Checks XDG_SESSION_DESKTOP, XDG_CURRENT_DESKTOP, DESKTOP_SESSION
    in that order (matching Plank Reloaded's priority).
    """
    for var in ("XDG_SESSION_DESKTOP", "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION"):
        value = os.environ.get(var, "")
        if value:
            desktop = _parse_desktop(value)
            if desktop != Desktop.UNKNOWN:
                log.debug("detected %s from %s=%s", desktop, var, value)
                return desktop

    log.warning("could not detect desktop environment")
    return Desktop.UNKNOWN


def _disable_xfce_dock_shadow() -> None:
    """Disable xfwm4's dock window shadow via xfconf-query."""
    if not shutil.which("xfconf-query"):
        log.warning("xfconf-query not found, cannot disable dock shadow")
        return
    try:
        subprocess.run(
            [
                "xfconf-query",
                "-c",
                "xfwm4",
                "-p",
                "/general/show_dock_shadow",
                "-s",
                "false",
            ],
            check=True,
            capture_output=True,
        )
        log.info("disabled xfce dock shadow")
    except subprocess.CalledProcessError as e:
        log.warning("failed to disable xfce dock shadow: %s", e)


def _check_compositor() -> None:
    """Warn if no compositing manager is active.

    Checks _NET_WM_CM_S{screen} selection owner via Xlib directly.
    Gdk.Screen.is_composited() can return stale True if the compositor
    crashed without releasing the selection, so we bypass GDK's cache.
    """
    try:
        import ctypes

        from gi.repository import GdkX11

        display = GdkX11.X11Display.get_default()
        if display is None:
            log.debug("skipping compositor check: no X11 display")
            return
        screen_num = display.get_default_screen()

        xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
        xdisplay = ctypes.c_void_p(hash(display.get_xdisplay()))

        xlib.XInternAtom.restype = ctypes.c_ulong
        xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        xlib.XGetSelectionOwner.restype = ctypes.c_ulong
        xlib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

        atom = xlib.XInternAtom(xdisplay, f"_NET_WM_CM_S{screen_num}".encode(), 0)
        owner = xlib.XGetSelectionOwner(xdisplay, atom)
        if owner != 0:
            return
    except Exception as exc:
        log.warning("failed to check compositor status: %s", exc)
        return

    log.warning(
        "no compositing manager detected (it may have crashed) -- "
        "dock may appear behind maximized windows and transparency may not work; "
        "enable compositing in your desktop settings or install a compositor "
        "(picom, compton, xcompmgr)"
    )


def apply_tweaks(desktop: Desktop) -> None:
    """Apply DE-specific tweaks at startup."""
    _check_compositor()
    if desktop & Desktop.XFCE:
        _disable_xfce_dock_shadow()
