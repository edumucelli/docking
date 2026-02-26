"""Window tracking via libwnck -- monitors running apps and active window."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Wnck", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Wnck  # noqa: E402

from docking.platform.launcher import DESKTOP_SUFFIX, GNOME_APP_PREFIX


def _wm_class_desktop_candidates(class_lower: str) -> list[str]:
    """Generate desktop ID candidates from a lowercased WM_CLASS.

    Handles apps whose WM_CLASS contains spaces (e.g. "mongodb compass",
    "aws vpn client") by trying hyphenated and no-space variants.
    Returns a deduplicated list of candidates to try.
    """
    candidates = [class_lower]
    if " " in class_lower:
        candidates.append(class_lower.replace(" ", "-"))
        candidates.append(class_lower.replace(" ", ""))
    # Deduplicate while preserving order
    seen: set[str] = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]


if TYPE_CHECKING:
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


class WindowTracker:
    """Tracks running applications and maps them to dock items via WM_CLASS."""

    def __init__(self, model: DockModel, launcher: Launcher) -> None:
        self._model = model
        self._launcher = launcher
        self._screen: Wnck.Screen | None = None
        self._wm_class_to_desktop: dict[str, str] = {}

        self._build_wm_class_map()
        # Defer screen init to after GTK is ready
        GLib.idle_add(self._init_screen)

    def _build_wm_class_map(self) -> None:
        """Build reverse map from WM_CLASS -> desktop_id for pinned items."""
        for item in self._model.visible_items():
            if item.wm_class:
                self._wm_class_to_desktop[item.wm_class.lower()] = item.desktop_id

    def _init_screen(self) -> bool:
        """Initialize Wnck screen and connect signals."""
        self._screen = Wnck.Screen.get_default()
        if self._screen is None:
            return False

        self._screen.force_update()
        self._screen.connect("window-opened", self._on_window_changed)
        self._screen.connect("window-closed", self._on_window_changed)
        self._screen.connect("active-window-changed", self._on_window_changed)

        # Initial scan
        self._update_running()
        return False

    def _on_window_changed(self, _screen: Wnck.Screen, *_args: Any) -> None:
        """Called when any window state changes."""
        self._update_running()

    def _update_running(self) -> None:
        """Scan all windows and update the dock model."""
        if self._screen is None:
            return

        active_window = self._screen.get_active_window()
        active_xid = active_window.get_xid() if active_window else 0

        # {desktop_id: {"count": n, "active": bool, "windows": [...]}}
        running: dict[str, dict[str, Any]] = {}

        for window in self._screen.get_windows():
            window_type = window.get_window_type()
            if window_type in (Wnck.WindowType.DESKTOP, Wnck.WindowType.DOCK):
                continue
            if window.is_skip_tasklist():
                continue

            desktop_id = self._match_window(window)
            if desktop_id is None:
                continue

            if desktop_id not in running:
                running[desktop_id] = {
                    "count": 0,
                    "active": False,
                    "urgent": False,
                    "windows": [],
                }

            running[desktop_id]["count"] += 1
            running[desktop_id]["windows"].append(window)
            if window.get_xid() == active_xid:
                running[desktop_id]["active"] = True
            if window.needs_attention():
                running[desktop_id]["urgent"] = True

        self._model.update_running(running)

    def _match_window(self, window: Wnck.Window) -> str | None:
        """Match a window to a desktop_id via WM_CLASS."""
        class_group = window.get_class_group_name()
        if not class_group:
            return None

        class_lower = class_group.lower()

        # Direct match
        if class_lower in self._wm_class_to_desktop:
            return self._wm_class_to_desktop[class_lower]

        # Try matching class instance name
        class_instance = window.get_class_instance_name()
        if class_instance:
            inst_lower = class_instance.lower()
            if inst_lower in self._wm_class_to_desktop:
                return self._wm_class_to_desktop[inst_lower]

        # Try to resolve via Gio: exact, hyphenated, no-spaces variants
        for candidate in _wm_class_desktop_candidates(class_lower):
            desktop_id = f"{candidate}{DESKTOP_SUFFIX}"
            info = self._launcher.resolve(desktop_id)
            if info:
                self._wm_class_to_desktop[class_lower] = info.desktop_id
                return info.desktop_id

        # Try with org.gnome prefix
        gnome_id = f"{GNOME_APP_PREFIX}{class_group}{DESKTOP_SUFFIX}"
        info = self._launcher.resolve(gnome_id)
        if info:
            self._wm_class_to_desktop[class_lower] = info.desktop_id
            return info.desktop_id

        return None

    def get_windows_for(self, desktop_id: str) -> list[Wnck.Window]:
        """Get all windows belonging to a desktop_id."""
        return self._get_windows_for(desktop_id)

    @staticmethod
    def activate_window(window: Wnck.Window) -> None:
        """Activate a specific window."""
        timestamp = Gtk.get_current_event_time() or 0
        if window.is_minimized():
            window.unminimize(timestamp)
        window.activate(timestamp)

    def toggle_focus(self, desktop_id: str) -> None:
        """Focus or minimize windows for a desktop_id (smart focus)."""
        if self._screen is None:
            return

        active_window = self._screen.get_active_window()
        windows = self._get_windows_for(desktop_id)

        if not windows:
            return

        # If any window of this app is active, minimize all
        if active_window and active_window in windows:
            for w in windows:
                w.minimize()
        else:
            # Activate the most recent window
            timestamp = Gtk.get_current_event_time() or 0
            windows[0].activate(timestamp)

    def close_all(self, desktop_id: str) -> None:
        """Close all windows for a desktop_id."""
        timestamp = Gtk.get_current_event_time() or 0
        for w in self._get_windows_for(desktop_id):
            w.close(timestamp)

    def _get_windows_for(self, desktop_id: str) -> list[Wnck.Window]:
        """Get all windows belonging to a desktop_id."""
        if self._screen is None:
            return []

        result: list[Wnck.Window] = []
        for window in self._screen.get_windows():
            window_type = window.get_window_type()
            # Skip DESKTOP/DOCK types -- Caja's desktop window can segfault on
            # WM_CLASS queries, and docks should never appear as app windows.
            if window_type in (Wnck.WindowType.DESKTOP, Wnck.WindowType.DOCK):
                continue
            if window.is_skip_tasklist():
                continue
            matched = self._match_window(window)
            if matched == desktop_id:
                result.append(window)
        return result
