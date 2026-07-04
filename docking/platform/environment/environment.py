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
import re
import shutil
import subprocess
from pathlib import Path

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
    WLROOTS = enum.auto()
    LABWC = enum.auto()
    SWAY = enum.auto()
    RIVER = enum.auto()
    WAYFIRE = enum.auto()
    HYPRLAND = enum.auto()
    NIRI = enum.auto()
    COSMIC = enum.auto()

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
    "wlroots": Desktop.WLROOTS,
    "labwc": Desktop.LABWC,
    "sway": Desktop.SWAY,
    "river": Desktop.RIVER,
    "wayfire": Desktop.WAYFIRE,
    "hyprland": Desktop.HYPRLAND,
    "niri": Desktop.NIRI,
    "cosmic": Desktop.COSMIC,
}


def _parse_desktop(value: str) -> Desktop:
    """Parse a desktop string, handling XDG desktop list separators."""
    result = Desktop.UNKNOWN
    for part in re.split(r"[:;]", value):
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


def is_wayland_session() -> bool:
    """Return True when the desktop session itself is Wayland."""
    return os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"


def is_flatpak() -> bool:
    """Return True when running inside a Flatpak sandbox."""
    return Path("/.flatpak-info").exists()


def is_gnome_session(*, desktop: Desktop | None = None) -> bool:
    """Return True for GNOME-shell-like sessions."""
    resolved = desktop if desktop is not None else detect_desktop()
    return bool(resolved & (Desktop.GNOME | Desktop.UBUNTU))


def is_mate_session(*, desktop: Desktop | None = None) -> bool:
    """Return True for MATE sessions."""
    resolved = desktop if desktop is not None else detect_desktop()
    return bool(resolved & Desktop.MATE)


def is_kde_session(*, desktop: Desktop | None = None) -> bool:
    """Return True for KDE sessions."""
    resolved = desktop if desktop is not None else detect_desktop()
    return bool(resolved & Desktop.KDE)


def is_x11_backend(*, display: object | None = None) -> bool:
    """Return True when the current GTK backend is X11."""
    if display is None:
        try:
            import gi

            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk

            display = Gdk.Display.get_default()
        except Exception:
            return False
    if display is None:
        return False
    cls = display.__class__
    if cls.__name__ == "X11Display":
        return True
    if cls.__module__.endswith("GdkX11"):
        return True
    return callable(getattr(display, "get_xdisplay", None))


def is_xwayland_session(*, display: object | None = None) -> bool:
    """Return True when Docking runs as an X11 client inside a Wayland session."""
    return is_wayland_session() and is_x11_backend(display=display)


def backend_name(*, display: object | None = None) -> str:
    """Return a compact backend class name for logging."""
    if display is None:
        try:
            import gi

            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk

            display = Gdk.Display.get_default()
        except Exception:
            return "unknown"
    if display is None:
        return "none"
    cls = display.__class__
    return f"{cls.__module__}.{cls.__name__}"


def compositor_active(*, display: object | None = None) -> bool | None:
    """Return compositor status for X11 backends, or None when unavailable."""
    try:
        import ctypes

        from gi.repository import GdkX11

        if display is None:
            display = GdkX11.X11Display.get_default()
        if display is None or not is_x11_backend(display=display):
            return None
        screen_num = display.get_default_screen()

        xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
        xdisplay = ctypes.c_void_p(hash(display.get_xdisplay()))

        xlib.XInternAtom.restype = ctypes.c_ulong
        xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        xlib.XGetSelectionOwner.restype = ctypes.c_ulong
        xlib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

        atom = xlib.XInternAtom(xdisplay, f"_NET_WM_CM_S{screen_num}".encode(), 0)
        owner = xlib.XGetSelectionOwner(xdisplay, atom)
        return owner != 0
    except Exception as exc:
        log.warning("failed to check compositor status: %s", exc)
        return None


def log_runtime_snapshot(
    *,
    display: object | None = None,
    desktop: Desktop | None = None,
) -> None:
    """Log a small startup snapshot for comparing good and bad runs."""
    compositor = compositor_active(display=display)
    log.info(
        "runtime snapshot: desktop=%s session=%s backend=%s x11=%s xwayland=%s "
        "compositor_active=%s DISPLAY=%r WAYLAND_DISPLAY=%r GDK_BACKEND=%r",
        desktop if desktop is not None else detect_desktop(),
        os.environ.get("XDG_SESSION_TYPE", "").strip().lower() or "unknown",
        backend_name(display=display),
        is_x11_backend(display=display),
        is_xwayland_session(display=display),
        compositor,
        os.environ.get("DISPLAY"),
        os.environ.get("WAYLAND_DISPLAY"),
        os.environ.get("GDK_BACKEND"),
    )


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
    active = compositor_active()
    if active is None:
        return
    if active:
        return

    log.warning(
        "no compositing manager detected (it may have crashed) -- "
        "dock transparency and opacity settings will not render "
        "(the dock will appear opaque); enable compositing in your "
        "desktop settings or install a compositor "
        "(picom, compton, xcompmgr)"
    )


def apply_tweaks(desktop: Desktop) -> None:
    """Apply DE-specific tweaks at startup."""
    _check_compositor()
    if desktop & Desktop.XFCE:
        _disable_xfce_dock_shadow()
