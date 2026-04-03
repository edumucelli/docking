"""Runtime window tracking and matching between Wnck windows and dock entries.

The core problem

The dock model is keyed by desktop-oriented identities such as:

    firefox.desktop
    org.gnome.Nautilus.desktop

But the running desktop gives the dock live windows, not desktop files:

    Wnck.Window

Some module has to answer:

    "Which running window belongs to which dock item?"

That translation layer is WindowTracker.

Why this is harder than it sounds

Desktop file names, WM_CLASS values, and window-manager class-group names are
related but not the same naming system.

Example:

    desktop file:   mongodb-compass.desktop
    WM_CLASS:       "mongodb compass"
    class-group:    "mongodb compass"

Another application may look like:

    desktop file:   org.gnome.Nautilus.desktop
    WM_CLASS:       "nautilus"
    class-group:    "Files"

So exact string equality is not enough. Matching has to be heuristic and
practical rather than theoretically perfect.

Identity sources used here

The most important runtime identity hints are:

- WM_CLASS instance name
- WM_CLASS class name
- class-group name from libwnck

ASCII view:

    running window
      |
      +--> WM_CLASS instance
      +--> WM_CLASS class
      +--> class-group
      |
      +--> candidate desktop ids
      |
      +--> matched dock desktop_id

Matching strategy

The tracker applies layered matching, strongest first:

1. direct cache lookup from previous successful matches
2. class-group / WM_CLASS lookup against known dock entries
3. synthesized desktop-id candidates from runtime names
4. GNOME-style prefixed candidates

This is deliberately heuristic because the runtime desktop is heuristic.

Examples of candidate synthesis:

    "mongodb compass"
      -> mongodb compass.desktop
      -> mongodb-compass.desktop
      -> mongodbcompass.desktop

This is enough to solve many real-world mismatches without hard-coding one app
at a time.

Aggregation model

The tracker does not push individual windows into the UI layer directly.
Instead, it builds one aggregate per matched desktop ID:

    {
      desktop_id: {
        "count": n,
        "active": bool,
        "urgent": bool,
        "windows": [...],
        "xids": [...],
      }
    }

That aggregate is what the model actually needs. From it, the dock can derive:

- running indicators,
- active-item state,
- urgent highlights,
- preview sources,
- click/toggle behavior.

Why full rescans are acceptable

Wnck emits:

- window-opened
- window-closed
- active-window-changed

On each relevant signal, this module rescans the current window list and
rebuilds the aggregate. That is a pragmatic design:

- simpler than maintaining many incremental partial updates,
- resilient to window-manager state changing underneath us,
- easy to reconcile back into the model.

This is a convergence-oriented module, not a fragile event-delta tracker.

Why defensive exception handling is required

Wnck objects are live wrappers around X11 state. A window can disappear between:

- getting the window list,
- reading the window type,
- reading WM_CLASS,
- reading active/urgent state,
- reading XID.

So this module intentionally treats many read failures as recoverable:

    log problem
      |
      +--> skip unstable window read
      |
      +--> continue rebuilding the aggregate

The dock cares more about quickly converging back to correct state than about
pretending the X11 world is stable during every scan.
"""

from __future__ import annotations

from itertools import chain, pairwise
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Wnck", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Wnck

from docking.log import get_logger, with_context
from docking.platform.launcher import DESKTOP_SUFFIX, GNOME_APP_PREFIX

# GLib.Error is not a real exception subclass in some PyGObject builds,
# so only add it to the catch tuple when it actually is one.
_RECOVERABLE_ERRORS: tuple[type[BaseException], ...] = (TypeError,)
if isinstance(GLib.Error, type) and issubclass(GLib.Error, BaseException):
    _RECOVERABLE_ERRORS = (TypeError, GLib.Error)

log = with_context(get_logger(name="window_tracker"))


def _wm_class_desktop_candidates(class_lower: str, class_group: str) -> list[str]:
    """Generate desktop ID candidates from a WM_CLASS.

    Handles apps whose WM_CLASS contains spaces (e.g. "mongodb compass",
    "aws vpn client") by trying hyphenated, no-space, and GNOME-prefixed
    variants. Returns a deduplicated list of candidates to try.

    class_group preserves original casing for GNOME-style IDs like
    org.gnome.Nautilus.desktop.
    """
    candidates = [class_lower]
    if " " in class_lower:
        candidates.append(class_lower.replace(" ", "-"))
        candidates.append(class_lower.replace(" ", ""))
    candidates.append(f"{GNOME_APP_PREFIX}{class_group}")
    # Deduplicate while preserving order
    return list(dict.fromkeys(candidates))


if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


class WindowTracker:
    """Tracks running applications and maps them to dock items via WM_CLASS."""

    def __init__(
        self, model: DockModel, launcher: Launcher, config: Config | None = None
    ) -> None:
        self._model = model
        self._config = config
        self._launcher = launcher
        self._screen: Wnck.Screen | None = None
        self._wm_class_to_desktop: dict[str, str] = {}
        self._missed_desktop_candidates: set[str] = set()
        # Latest known window XIDs per desktop_id from _update_running().
        # Preview/toggle paths use this cache to avoid rematching WM_CLASS
        # during hover-time UI events.
        self._running_xids_by_desktop: dict[str, list[int]] = {}

        self._build_wm_class_map()
        # Defer screen init to after GTK is ready
        GLib.idle_add(self._init_screen)

    def _build_wm_class_map(self) -> None:
        """Build reverse map from WM_CLASS -> desktop_id for pinned items."""
        self._wm_class_to_desktop.clear()
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
        # Keep WM_CLASS mapping in sync with current visible items. The set of
        # pinned/transient items can change at runtime (pin/unpin/reorder),
        # and a stale map causes running indicators to miss windows.
        self._build_wm_class_map()

        if self._screen is None:
            return

        active_window = self._screen.get_active_window()
        active_xid = 0
        if active_window:
            try:
                active_xid = active_window.get_xid()
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="active_xid").warning(
                    f"Failed to read active window xid: {exc}"
                )
                active_xid = 0

        # {desktop_id: {"count": n, "active": bool, "urgent": bool,
        #               "windows": [...], "xids": [...]}}
        running: dict[str, dict[str, Any]] = {}

        for window in self._screen.get_windows():
            try:
                window_type = window.get_window_type()
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="window_type").warning(
                    f"Skipping window: failed to read window type: {exc}"
                )
                continue
            if window_type in (Wnck.WindowType.DESKTOP, Wnck.WindowType.DOCK):
                continue
            try:
                if window.is_skip_tasklist():
                    continue
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="skip_tasklist").warning(
                    f"Skipping window: failed to read skip-tasklist state: {exc}"
                )
                continue

            desktop_id = self._match_window(window=window)
            if desktop_id is None:
                continue

            if desktop_id not in running:
                running[desktop_id] = {
                    "count": 0,
                    "active": False,
                    "urgent": False,
                    "windows": [],
                    "xids": [],
                }

            try:
                xid = window.get_xid()
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="window_xid").warning(
                    f"Skipping window: failed to read xid: {exc}"
                )
                continue
            running[desktop_id]["count"] += 1
            running[desktop_id]["windows"].append(window)
            running[desktop_id]["xids"].append(xid)
            if xid == active_xid:
                running[desktop_id]["active"] = True
            try:
                if window.needs_attention():
                    running[desktop_id]["urgent"] = True
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="needs_attention", desktop_id=desktop_id).warning(
                    f"Failed to read urgent state: {exc}"
                )

        self._running_xids_by_desktop = {
            desktop_id: list(info.get("xids", []))
            for desktop_id, info in running.items()
        }
        self._model.update_running(running=running)

    def _match_window(self, window: Wnck.Window) -> str | None:
        """Match a window to a desktop_id via WM_CLASS."""
        try:
            class_group = window.get_class_group_name()
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="class_group").warning(
                f"Skipping window: failed to read class group: {exc}"
            )
            return None
        if not class_group:
            return None

        class_lower = class_group.lower()

        # Direct match
        if class_lower in self._wm_class_to_desktop:
            return self._wm_class_to_desktop[class_lower]

        # Try matching class instance name
        try:
            class_instance = window.get_class_instance_name()
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="class_instance").warning(
                f"Failed to read class instance name: {exc}"
            )
            class_instance = None
        if class_instance:
            inst_lower = class_instance.lower()
            if inst_lower in self._wm_class_to_desktop:
                desktop_id = self._wm_class_to_desktop[inst_lower]
                # Cache under class_lower too so future lookups hit directly.
                self._wm_class_to_desktop[class_lower] = desktop_id
                return desktop_id

        # Try to resolve via Gio: exact, hyphenated, no-spaces, GNOME-prefixed
        candidate_ids = [
            f"{candidate}{DESKTOP_SUFFIX}"
            for candidate in _wm_class_desktop_candidates(
                class_lower=class_lower, class_group=class_group
            )
        ]
        for desktop_id, next_candidate in pairwise(chain(candidate_ids, [None])):
            if desktop_id in self._missed_desktop_candidates:
                continue
            info = self._launcher.resolve(desktop_id=desktop_id, log_failures=False)
            if info:
                self._wm_class_to_desktop[class_lower] = info.desktop_id
                return info.desktop_id
            self._missed_desktop_candidates.add(desktop_id)
            if next_candidate:
                log.bind(action="match_window", desktop_id=desktop_id).debug(
                    "Desktop candidate miss for class_group=%s (class_lower=%s); "
                    "next candidate: %s",
                    class_group,
                    class_lower,
                    next_candidate,
                )
            else:
                log.bind(action="match_window", desktop_id=desktop_id).debug(
                    "Desktop candidate miss for class_group=%s (class_lower=%s); "
                    "no more candidates",
                    class_group,
                    class_lower,
                )

        return None

    def get_windows_for(self, desktop_id: str) -> list[Wnck.Window]:
        """Get all windows belonging to a desktop_id."""
        return self._get_windows_for(desktop_id=desktop_id)

    def get_xids_for(self, desktop_id: str) -> list[int]:
        """Get current window XIDs for a desktop_id from the latest scan."""
        return list(self._running_xids_by_desktop.get(desktop_id, []))

    def icon_name_for_desktop(self, desktop_id: str) -> str:
        """Return icon name for desktop_id from the current dock model."""
        for item in self._model.visible_items():
            if item.desktop_id == desktop_id:
                return item.icon_name or "application-x-executable"
        return "application-x-executable"

    def get_window_title_for_xid(self, xid: int) -> str:
        """Get a best-effort window title for an XID."""
        window = self._window_for_xid(xid=xid)
        if window is None:
            return "Window"
        try:
            title = window.get_name()
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="window_title", xid=str(xid)).warning(
                f"Failed to read window title: {exc}"
            )
            return "Window"
        return title or "Window"

    @staticmethod
    def activate_window(window: Wnck.Window) -> None:
        """Activate a specific window."""
        timestamp = Gtk.get_current_event_time() or 0
        try:
            if window.is_minimized():
                window.unminimize(timestamp)
            window.activate(timestamp)
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="activate_window").warning(
                f"Failed to activate window: {exc}"
            )
            return

    def activate_xid(self, xid: int) -> None:
        """Activate a window by XID, if still present."""
        window = self._window_for_xid(xid=xid)
        if window is None:
            return
        self.activate_window(window=window)

    def toggle_focus(self, desktop_id: str) -> None:
        """Focus or minimize windows for a desktop_id (smart focus)."""
        if self._screen is None:
            return

        active_window = self._screen.get_active_window()
        windows = self._get_windows_for(desktop_id=desktop_id)

        if not windows:
            return

        # If any window of this app is active, minimize all
        if active_window and active_window in windows:
            for w in windows:
                try:
                    w.minimize()
                except _RECOVERABLE_ERRORS as exc:
                    log.bind(action="minimize", desktop_id=desktop_id).warning(
                        f"Failed to minimize window: {exc}"
                    )
        else:
            # Activate the most recent window
            self.activate_window(window=windows[0])

    def close_all(self, desktop_id: str) -> None:
        """Close all windows for a desktop_id."""
        timestamp = Gtk.get_current_event_time() or 0
        for w in self._get_windows_for(desktop_id=desktop_id):
            try:
                w.close(timestamp)
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="close_all", desktop_id=desktop_id).warning(
                    f"Failed to close window: {exc}"
                )

    def close_xid(self, xid: int) -> None:
        """Close a specific window by XID, if still present."""
        window = self._window_for_xid(xid=xid)
        if window is None:
            return
        timestamp = Gtk.get_current_event_time() or 0
        try:
            window.close(timestamp)
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="close_xid", xid=str(xid)).warning(
                f"Failed to close window: {exc}"
            )

    def _get_windows_for(self, desktop_id: str) -> list[Wnck.Window]:
        """Get windows for desktop_id using cached XIDs from last scan.

        This avoids rematching WM_CLASS in hover/preview paths, which can
        race with native Wnck object lifetime transitions.
        """
        if self._screen is None:
            return []

        # Build current XID -> window map from live screen windows.
        by_xid: dict[int, Wnck.Window] = {}
        for window in self._screen.get_windows():
            try:
                window_type = window.get_window_type()
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="get_windows_type").warning(
                    f"Skipping window while collecting by xid: {exc}"
                )
                continue
            # Skip DESKTOP/DOCK types -- Caja's desktop window can segfault on
            # WM_CLASS queries, and docks should never appear as app windows.
            if window_type in (Wnck.WindowType.DESKTOP, Wnck.WindowType.DOCK):
                continue
            try:
                if window.is_skip_tasklist():
                    continue
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="get_windows_skip_tasklist").warning(
                    f"Skipping window while collecting by xid: {exc}"
                )
                continue
            try:
                by_xid[window.get_xid()] = window
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="get_windows_xid").warning(
                    f"Skipping window while collecting xid map: {exc}"
                )
                continue

        result: list[Wnck.Window] = []
        active_ws = (
            self._screen.get_active_workspace()
            if self._config and self._config.current_workspace_only
            else None
        )
        for xid in self._running_xids_by_desktop.get(desktop_id, []):
            window = by_xid.get(xid)
            if window is not None:
                if active_ws is not None and not window.is_on_workspace(active_ws):
                    continue
                result.append(window)
        return result

    def _window_for_xid(self, xid: int) -> Wnck.Window | None:
        """Resolve a live Wnck window by XID."""
        if self._screen is None:
            return None
        for window in self._screen.get_windows():
            try:
                if window.get_xid() == xid:
                    return window
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="window_for_xid", xid=str(xid)).warning(
                    f"Failed while scanning windows for xid: {exc}"
                )
                continue
        return None
