"""Desktop file resolution and application launching via XDG and Gio."""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk, GdkPixbuf, GLib  # noqa: E402

DESKTOP_SUFFIX = ".desktop"
FALLBACK_ICON = "application-x-executable"
DEFAULT_XDG_DATA_DIRS = "/usr/local/share:/usr/share"
GNOME_APP_PREFIX = "org.gnome."


class DesktopInfo(NamedTuple):
    """Resolved information from a .desktop file."""

    desktop_id: str
    name: str
    icon_name: str
    wm_class: str
    exec_line: str


class Launcher:
    """Resolves .desktop files via XDG_DATA_DIRS and loads icons."""

    def __init__(self) -> None:
        self._desktop_dirs = self._get_desktop_dirs()
        self._icon_cache: dict[tuple[str, int], GdkPixbuf.Pixbuf | None] = {}

    def resolve(self, desktop_id: str) -> DesktopInfo | None:
        """Resolve a desktop ID (e.g. 'firefox.desktop') to full info."""
        try:
            app_info = Gio.DesktopAppInfo.new(desktop_id)
        except (TypeError, GLib.Error):
            app_info = None
        if app_info is None:
            # Try searching by filename in XDG dirs
            for d in self._desktop_dirs:
                path = d / desktop_id
                if path.exists():
                    try:
                        app_info = Gio.DesktopAppInfo.new_from_filename(str(path))
                    except (TypeError, GLib.Error):
                        continue
                    break
        if app_info is None:
            return None

        wm_class = app_info.get_startup_wm_class() or ""
        if not wm_class:
            # Fallback: derive from executable name
            commandline = app_info.get_commandline() or ""
            exe = commandline.split()[0] if commandline else ""
            wm_class = (
                Path(exe).name if exe else desktop_id.removesuffix(DESKTOP_SUFFIX)
            )

        icon = app_info.get_icon()
        icon_name = icon.to_string() if icon else FALLBACK_ICON

        return DesktopInfo(
            desktop_id=desktop_id,
            name=app_info.get_display_name() or desktop_id,
            icon_name=icon_name,
            wm_class=wm_class,
            exec_line=app_info.get_commandline() or "",
        )

    def load_icon(self, icon_name: str, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load an icon by name at the given size, with caching."""
        key = (icon_name, size)
        if key in self._icon_cache:
            return self._icon_cache[key]

        pixbuf = self._try_load_icon(icon_name, size)
        self._icon_cache[key] = pixbuf
        return pixbuf

    def _try_load_icon(self, icon_name: str, size: int) -> GdkPixbuf.Pixbuf | None:
        """Attempt to load icon from theme or file path."""
        theme = Gtk.IconTheme.get_default()

        # If it's an absolute path
        if os.path.isabs(icon_name) and os.path.exists(icon_name):
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    icon_name, size, size, True
                )
            except GLib.Error:
                pass

        # Try icon theme lookup
        try:
            return theme.load_icon(icon_name, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error:
            pass

        # Fallback
        try:
            return theme.load_icon(FALLBACK_ICON, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error:
            return None

    @staticmethod
    def _get_desktop_dirs() -> list[Path]:
        """Get application .desktop file directories from XDG_DATA_DIRS."""
        xdg = os.environ.get("XDG_DATA_DIRS", DEFAULT_XDG_DATA_DIRS)
        dirs = []
        for d in xdg.split(":"):
            p = Path(d) / "applications"
            if p.is_dir():
                dirs.append(p)
        # Also check user-local
        local = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        user_apps = local / "applications"
        if user_apps.is_dir():
            dirs.insert(0, user_apps)
        return dirs


def get_actions(desktop_id: str) -> list[tuple[str, str]]:
    """Return desktop actions for an app as [(action_id, display_name), ...]."""
    try:
        app_info = Gio.DesktopAppInfo.new(desktop_id)
    except (TypeError, GLib.Error):
        return []
    if not app_info:
        return []
    result = []
    for action_id in app_info.list_actions():
        name = app_info.get_action_name(action_id)
        if name:
            result.append((action_id, name))
    return result


def launch_action(desktop_id: str, action_id: str) -> None:
    """Launch a specific desktop action for an app."""
    try:
        app_info = Gio.DesktopAppInfo.new(desktop_id)
    except (TypeError, GLib.Error):
        return
    if app_info:
        try:
            app_info.launch_action(action_id, None)
        except GLib.Error:
            pass


def launch(desktop_id: str) -> None:
    """Launch an application by its desktop ID.

    Uses subprocess with start_new_session=True so the child gets its own
    session and process group. This prevents the child from receiving
    SIGHUP/SIGINT when the dock exits or the terminal sends Ctrl+C.
    """
    import subprocess

    app_info = Gio.DesktopAppInfo.new(desktop_id)
    if not app_info:
        return
    cmdline = app_info.get_commandline()
    if not cmdline:
        return
    # Strip %u, %U, %f, %F field codes from Exec line
    import re

    cmd = re.sub(r"%[uUfFdDnNickvm]", "", cmdline).strip()
    try:
        subprocess.Popen(
            cmd,
            shell=True,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        print(f"Failed to launch {desktop_id}: {e}")
