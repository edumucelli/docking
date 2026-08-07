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

"""Runtime window tracking and matching between Wnck windows and dock entries.

The core problem

The dock model is keyed by desktop-oriented identities such as:

    firefox.desktop
    org.gnome.Nautilus.desktop

But the running desktop gives the dock live windows, not desktop files:

    Wnck.Window

Some module has to answer:

    "Which running window belongs to which dock item?"

That translation layer is split between:

- `WindowTracker`, which scans and orders live Wnck windows.
- `WindowMatcher`, which maps each window's runtime identity to a desktop ID.

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

1. visible dock item aliases from pinned and transient entries
2. Wine class-instance disambiguation for generic `Wine` windows
3. synthesized desktop-id candidates from runtime names
4. install-wide WM_CLASS / executable alias index

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

    dict[desktop_id, RunningAppInfo]

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

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Wnck", "3.0")
gi.require_version("Gtk", "3.0")
import os

from gi.repository import GLib, Gtk, Wnck

from docking.log import get_logger, with_context
from docking.platform.applications.matcher import AppIdMatcher
from docking.platform.applications.running import RunningAppInfo, RunningWindowInfo
from docking.platform.applications.types import ApplicationMatch
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    Rect,
    WindowId,
    WindowSnapshot,
)

# GLib.Error is not a real exception subclass in some PyGObject builds,
# so only add it to the catch tuple when it actually is one.
_RECOVERABLE_ERRORS: tuple[type[BaseException], ...] = (TypeError,)
if isinstance(GLib.Error, type) and issubclass(GLib.Error, BaseException):
    _RECOVERABLE_ERRORS = (TypeError, GLib.Error)
_GEOMETRY_ERRORS: tuple[type[BaseException], ...] = (ValueError, *_RECOVERABLE_ERRORS)

log = with_context(get_logger(name="window_tracker"))


if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel


class WindowMatcher:
    """Matches live Wnck windows to desktop IDs using WM_CLASS-like hints.

    This is a thin X11-specific wrapper around the shared
    :class:`~docking.platform.applications.matcher.AppIdMatcher`. The wrapping
    handles Wnck-specific window property extraction (class group,
    class instance) and defensive error handling; all matching
    heuristics live in the shared matcher so they apply uniformly
    across X11 and Wayland backends.
    """

    def __init__(
        self,
        *,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
    ) -> None:
        self._app_matcher = AppIdMatcher(
            registry=application_registry,
            process_identity_service=process_identity_service,
            cache_missed_desktop_ids=True,
        )

    def sync_visible_items(self, items: Iterable[DockItem]) -> None:
        """Refresh pinned/transient alias hints from current dock items."""
        self._app_matcher.sync_visible_items(items)

    def match(self, window: Wnck.Window) -> str | None:
        """Return the desktop ID for a window, or None when no match is known."""
        result = self.match_result(window)
        return result.desktop_id if result is not None else None

    def match_result(self, window: Wnck.Window) -> ApplicationMatch | None:
        """Return structured identity, including runtime-only app metadata."""
        class_group = self._class_group_for(window=window)
        if not class_group:
            return None
        class_instance = self._class_instance_for(window=window)
        return self._app_matcher.match_result(
            app_id=class_group,
            instance_hint=class_instance,
            prefer_raw_app_id=False,
            defer_wm_class_lookup=True,
            process_id=self._pid_for(window=window),
        )

    def _class_group_for(self, *, window: Wnck.Window) -> str | None:
        try:
            return window.get_class_group_name() or None
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="class_group").warning(
                f"Skipping window: failed to read class group: {exc}"
            )
            return None

    def _class_instance_for(self, *, window: Wnck.Window) -> str | None:
        try:
            return window.get_class_instance_name() or None
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="class_instance").warning(
                f"Failed to read class instance name: {exc}"
            )
            return None

    def _pid_for(self, *, window: Wnck.Window) -> int | None:
        try:
            pid = int(window.get_pid())
        except _GEOMETRY_ERRORS as exc:
            log.bind(action="window_pid").debug(
                "Failed to read window process ID: %s",
                exc,
            )
            return None
        return pid if pid > 0 else None


class WindowTracker:
    """Tracks running applications and maps them to dock items via WM_CLASS."""

    def __init__(
        self,
        model: DockModel,
        config: Config | None = None,
        *,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
    ) -> None:
        if config is None:
            raise TypeError("config is required")
        self._model = model
        self._config = config
        self._screen: Wnck.Screen | None = None
        self._screen_signal_ids: list[int] = []
        self._matcher = WindowMatcher(
            application_registry=application_registry,
            process_identity_service=process_identity_service,
        )
        # Latest known window XIDs per desktop_id from _update_running().
        # Preview/toggle paths use this cache to avoid rematching WM_CLASS
        # during hover-time UI events.
        self._running_xids_by_desktop: dict[str, list[int]] = {}
        self._last_running: dict[str, RunningAppInfo] = {}
        self._cycle_index: dict[str, int] = {}
        self._cycle_order_by_desktop: dict[str, list[int]] = {}

        self._matcher.sync_visible_items(self._model.visible_items())
        # Defer screen init to after GTK is ready
        GLib.idle_add(self._init_screen)

    def _init_screen(self) -> bool:
        """Initialize Wnck screen and connect signals."""
        if self._screen is not None:
            return False

        self._screen = Wnck.Screen.get_default()
        if self._screen is None:
            return False

        self._screen.force_update()
        self._screen_signal_ids = []
        self._screen_signal_ids.append(
            self._screen.connect("window-opened", self._on_window_changed)
        )
        self._screen_signal_ids.append(
            self._screen.connect("window-closed", self._on_window_changed)
        )
        self._screen_signal_ids.append(
            self._screen.connect("active-window-changed", self._on_window_changed)
        )

        # Initial scan
        self._update_running()
        return False

    def _disconnect_screen_signals(self) -> None:
        if self._screen is None:
            self._screen_signal_ids = []
            return
        for handler_id in self._screen_signal_ids:
            try:
                self._screen.disconnect(handler_id)
            except Exception as exc:
                log.bind(action="disconnect_screen").debug(
                    "Failed to disconnect window-tracker signal: %s",
                    exc,
                )
        self._screen_signal_ids = []

    def _on_window_changed(self, _screen: Wnck.Screen, *_args: Any) -> None:
        """Called when any window state changes."""
        self._update_running()

    def _update_running(self) -> None:
        """Scan all windows and update the dock model."""
        # Keep WM_CLASS mapping in sync with current visible items. The set of
        # pinned/transient items can change at runtime (pin/unpin/reorder),
        # and a stale map causes running indicators to miss windows.
        self._matcher.sync_visible_items(self._model.visible_items())

        if self._screen is None:
            return

        active_xid = self._active_xid()
        active_workspace = (
            self._screen.get_active_workspace()
            if self._config.current_workspace_only
            else None
        )
        snapshots_by_desktop: dict[str, list[RunningWindowInfo]] = {}
        for window in self._iter_tasklist_windows():
            if active_workspace is not None and not self._window_on_workspace(
                window=window,
                workspace=active_workspace,
            ):
                continue
            match = self._matcher.match_result(window=window)
            if match is None:
                continue
            desktop_id = match.desktop_id
            # Preserve the old scan semantics: once a window matched a desktop
            # ID, the app existed in the aggregate even if a later XID read
            # failed. That can produce count=0 for a racey window, but it avoids
            # changing visible state just because X11 invalidated one property
            # mid-scan.
            snapshots_by_desktop.setdefault(desktop_id, [])
            snapshot = self._window_snapshot(
                window=window,
                desktop_id=desktop_id,
                active_xid=active_xid,
                match=match,
            )
            if snapshot is not None:
                snapshots_by_desktop[desktop_id].append(snapshot)

        running = self._aggregate_running(windows_by_desktop=snapshots_by_desktop)
        self._last_running = dict(running)
        self._running_xids_by_desktop = {
            desktop_id: list(info.xids) for desktop_id, info in running.items()
        }
        self._cleanup_cycle_state(active_desktop_ids=set(running))
        self._model.update_running(running=running)

    def _active_xid(self) -> int:
        """Return the active window XID for the current scan."""
        if self._screen is None:
            return 0
        active_window = self._screen.get_active_window()
        if active_window is None:
            return 0
        try:
            return int(active_window.get_xid())
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="active_xid").warning(
                f"Failed to read active window xid: {exc}"
            )
            return 0

    def _iter_tasklist_windows(self) -> Iterable[Wnck.Window]:
        """Yield windows that should count as application tasklist windows."""
        if self._screen is None:
            return
        own_pid = os.getpid()
        for window in self._screen.get_windows():
            try:
                window_type = window.get_window_type()
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="window_type").warning(
                    f"Skipping window: failed to read window type: {exc}"
                )
                continue
            # Desktop and dock windows are not application instances. This also
            # protects later matching code from desktop-shell windows that can
            # be unsafe to query for WM_CLASS on some environments.
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
            # Never track windows belonging to Docking itself (settings dialog, etc.).
            try:
                if window.get_pid() == own_pid:
                    continue
            except _RECOVERABLE_ERRORS:
                pass
            yield window

    def _window_snapshot(
        self,
        *,
        window: Wnck.Window,
        desktop_id: str,
        active_xid: int,
        match: ApplicationMatch,
    ) -> RunningWindowInfo | None:
        """Convert one live Wnck window into typed running state."""
        # Wnck windows are live wrappers around X11 state. The window can vanish
        # after filtering and matching but before this XID read, so a failed
        # property read skips only this window snapshot, not the whole scan.
        try:
            xid = int(window.get_xid())
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="window_xid").warning(
                f"Skipping window: failed to read xid: {exc}"
            )
            return None
        # Urgency is optional UI state. If it races, keep the matched running
        # window and only drop the urgent flag for this scan.
        urgent = False
        try:
            urgent = bool(window.needs_attention())
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="needs_attention", desktop_id=desktop_id).warning(
                f"Failed to read urgent state: {exc}"
            )
        return RunningWindowInfo(
            desktop_id=desktop_id,
            xid=xid,
            window_id=WindowId.x11(xid),
            active=xid == active_xid,
            urgent=urgent,
            window=window,
            runtime_app=match.runtime_app,
        )

    @staticmethod
    def _aggregate_running(
        *, windows_by_desktop: dict[str, list[RunningWindowInfo]]
    ) -> dict[str, RunningAppInfo]:
        """Group matched windows into the model's per-app running state."""
        return {
            desktop_id: RunningAppInfo.from_windows(items)
            for desktop_id, items in windows_by_desktop.items()
        }

    def _cleanup_cycle_state(self, *, active_desktop_ids: set[str]) -> None:
        """Drop stale per-app cycle state after a full scan."""
        for desktop_id in list(self._cycle_order_by_desktop):
            if desktop_id not in active_desktop_ids:
                self._cycle_order_by_desktop.pop(desktop_id, None)
                self._cycle_index.pop(desktop_id, None)

    def list_all_windows(self) -> list[WindowSnapshot]:
        """Return backend-neutral snapshots for every matched X11 window."""
        snapshots: list[WindowSnapshot] = []
        for desktop_id in self._running_xids_by_desktop:
            snapshots.extend(self.list_windows(desktop_id=desktop_id))
        return snapshots

    def list_windows(self, desktop_id: str) -> list[WindowSnapshot]:
        """Return backend-neutral snapshots for current windows of a desktop ID."""
        active_xid = self._active_xid()
        snapshots: list[WindowSnapshot] = []
        for window in self._get_windows_for(desktop_id=desktop_id):
            snapshot = self._window_service_snapshot(
                window=window,
                desktop_id=desktop_id,
                active_xid=active_xid,
            )
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def list_preview_windows(self, desktop_id: str) -> list[WindowSnapshot]:
        """Return preview windows using the old cached-XID preview semantics."""
        active_xid = self._active_xid()
        snapshots: list[WindowSnapshot] = []
        for xid in self._running_xids_by_desktop.get(desktop_id, []):
            window = self._window_for_xid(xid=xid)
            if window is None:
                snapshots.append(
                    WindowSnapshot(
                        id=WindowId.x11(xid),
                        desktop_id=desktop_id,
                        title="Window",
                        can_activate=True,
                        can_close=True,
                        can_preview=True,
                    )
                )
                continue
            snapshot = self._window_service_snapshot(
                window=window,
                desktop_id=desktop_id,
                active_xid=active_xid,
            )
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def icon_name_for_desktop(self, desktop_id: str) -> str:
        """Return icon name for desktop_id from the current dock model."""
        for item in self._model.visible_items():
            if item.desktop_id == desktop_id:
                return item.icon_name or "application-x-executable"
        return "application-x-executable"

    @staticmethod
    def _window_on_workspace(*, window: Wnck.Window, workspace: Wnck.Workspace) -> bool:
        try:
            return bool(window.is_on_workspace(workspace))
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="window_workspace_filter").warning(
                f"Skipping window: failed to read workspace membership: {exc}"
            )
            return False

    def _window_service_snapshot(
        self, *, window: Wnck.Window, desktop_id: str, active_xid: int
    ) -> WindowSnapshot | None:
        """Convert one live Wnck window into the backend-neutral service shape."""
        try:
            xid = int(window.get_xid())
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="service_window_xid").warning(
                f"Skipping window service snapshot: failed to read xid: {exc}"
            )
            return None

        title = self._read_window_title(window=window, xid=xid)
        app_id = self._read_window_string(
            window=window,
            method_name="get_class_group_name",
            action="service_app_id",
            xid=xid,
        )
        wm_class = self._read_window_string(
            window=window,
            method_name="get_class_instance_name",
            action="service_wm_class",
            xid=xid,
        )
        return WindowSnapshot(
            id=WindowId.x11(xid),
            desktop_id=desktop_id,
            title=title,
            app_id=app_id,
            wm_class=wm_class or app_id,
            active=xid == active_xid,
            urgent=self._read_window_bool(
                window=window,
                method_name="needs_attention",
                action="service_urgent",
                xid=xid,
                default=False,
            ),
            minimized=self._read_window_optional_bool(
                window=window,
                method_name="is_minimized",
                action="service_minimized",
                xid=xid,
            ),
            maximized=self._read_window_optional_bool(
                window=window,
                method_name="is_maximized",
                action="service_maximized",
                xid=xid,
            ),
            fullscreen=self._read_window_optional_bool(
                window=window,
                method_name="is_fullscreen",
                action="service_fullscreen",
                xid=xid,
            ),
            geometry=self._read_window_geometry(window=window, xid=xid),
            workspace_id=self._read_window_workspace_id(window=window, xid=xid),
            can_activate=True,
            can_minimize=True,
            can_close=True,
            can_preview=True,
        )

    @staticmethod
    def _read_window_title(*, window: Wnck.Window, xid: int) -> str:
        try:
            title = window.get_name()
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="service_title", xid=str(xid)).warning(
                f"Failed to read window title: {exc}"
            )
            return "Window"
        return title or "Window"

    @staticmethod
    def _read_window_string(
        *, window: Wnck.Window, method_name: str, action: str, xid: int
    ) -> str | None:
        method = getattr(window, method_name, None)
        if method is None:
            return None
        try:
            value = method()
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action=action, xid=str(xid)).warning(
                f"Failed to read window string property: {exc}"
            )
            return None
        return value or None

    @staticmethod
    def _read_window_bool(
        *,
        window: Wnck.Window,
        method_name: str,
        action: str,
        xid: int,
        default: bool,
    ) -> bool:
        method = getattr(window, method_name, None)
        if method is None:
            return default
        try:
            return bool(method())
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action=action, xid=str(xid)).warning(
                f"Failed to read window boolean property: {exc}"
            )
            return default

    @classmethod
    def _read_window_optional_bool(
        cls, *, window: Wnck.Window, method_name: str, action: str, xid: int
    ) -> bool | None:
        method = getattr(window, method_name, None)
        if method is None:
            return None
        return cls._read_window_bool(
            window=window,
            method_name=method_name,
            action=action,
            xid=xid,
            default=False,
        )

    @staticmethod
    def _read_window_geometry(*, window: Wnck.Window, xid: int) -> Rect | None:
        get_geometry = getattr(window, "get_geometry", None)
        if get_geometry is None:
            return None
        try:
            x, y, width, height = get_geometry()
        except _GEOMETRY_ERRORS as exc:
            log.bind(action="service_geometry", xid=str(xid)).warning(
                f"Failed to read window geometry: {exc}"
            )
            return None
        return Rect(x=int(x), y=int(y), width=int(width), height=int(height))

    @staticmethod
    def _read_window_workspace_id(*, window: Wnck.Window, xid: int) -> str | None:
        get_workspace = getattr(window, "get_workspace", None)
        if get_workspace is None:
            return None
        try:
            workspace = get_workspace()
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="service_workspace", xid=str(xid)).warning(
                f"Failed to read window workspace: {exc}"
            )
            return None
        if workspace is None:
            return None
        get_number = getattr(workspace, "get_number", None)
        if get_number is not None:
            try:
                return str(get_number())
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="service_workspace_number", xid=str(xid)).warning(
                    f"Failed to read window workspace number: {exc}"
                )
        get_name = getattr(workspace, "get_name", None)
        if get_name is not None:
            try:
                return get_name() or None
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="service_workspace_name", xid=str(xid)).warning(
                    f"Failed to read window workspace name: {exc}"
                )
        return None

    @staticmethod
    def activate_window(window: Wnck.Window) -> None:
        """Activate a specific window."""
        timestamp = Gtk.get_current_event_time() or 0
        try:
            # TODO: If this window belongs to another workspace, activate that
            # workspace before activating the window so dock clicks can reveal
            # already-running apps outside the current workspace.
            if window.is_minimized():
                window.unminimize(timestamp)
            window.activate(timestamp)
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="activate_window").warning(
                f"Failed to activate window: {exc}"
            )
            return

    def activate(self, window_id: WindowId) -> ActionResult:
        """Activate one X11 window by backend-neutral window ID."""
        xid = self._xid_from_window_id(window_id)
        if xid is None:
            return ActionResult.UNSUPPORTED
        window = self._window_for_xid(xid=xid)
        if window is None:
            return ActionResult.NOT_FOUND
        self.activate_window(window=window)
        return ActionResult.OK

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        """Focus or minimize windows for a desktop_id (smart focus)."""
        if self._screen is None:
            return ActionResult.UNSUPPORTED

        active_window = self._screen.get_active_window()
        windows = self._get_windows_for(desktop_id=desktop_id)

        if not windows:
            return ActionResult.NOT_FOUND

        # If any window of this app is active, minimize all
        if active_window and active_window in windows:
            self._minimize_windows(desktop_id=desktop_id)
        else:
            # Activate the most recent window
            self.activate_window(window=windows[0])
        return ActionResult.OK

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        """Focus the MRU window for desktop_id, or minimize if already active."""
        if self._screen is None:
            return ActionResult.UNSUPPORTED

        windows = self._get_windows_for(desktop_id=desktop_id)
        if not windows:
            return ActionResult.NOT_FOUND

        active_window = self._screen.get_active_window()
        if active_window and active_window in windows:
            self._minimize_windows(desktop_id=desktop_id)
            return ActionResult.OK

        target = self._most_recent_window(windows=windows)
        self.activate_window(window=target)
        return ActionResult.OK

    def _most_recent_window(self, windows: list[Wnck.Window]) -> Wnck.Window:
        """Return the topmost window in stacking order from the candidates."""
        if self._screen is None or len(windows) == 1:
            return windows[0]
        candidate_xids = {self._xid_for(window=w) for w in windows}
        candidate_xids.discard(0)
        if not candidate_xids:
            return windows[0]
        try:
            stacked = self._screen.get_windows_stacked()
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="windows_stacked").warning(
                f"Failed to read stacked windows: {exc}"
            )
            return windows[0]
        by_xid = {self._xid_for(window=w): w for w in windows}
        for window in reversed(stacked):
            xid = self._xid_for(window=window)
            if xid in candidate_xids:
                return by_xid.get(xid, windows[0])
        return windows[0]

    def _cycle_windows(self, desktop_id: str) -> None:
        """Cycle through windows for a desktop_id, minimizing on wrap."""
        if self._screen is None:
            return

        windows = self._ordered_windows_for(desktop_id=desktop_id)
        if not windows:
            return
        if len(windows) == 1:
            self.toggle_focus(desktop_id=desktop_id)
            return

        active_window = self._screen.get_active_window()
        active_xid = self._xid_for(window=active_window)
        window_xids = [self._xid_for(window=window) for window in windows]

        if active_xid in window_xids:
            current_index = window_xids.index(active_xid)
            self._cycle_index[desktop_id] = current_index
            if current_index == len(windows) - 1:
                self._minimize_windows(desktop_id=desktop_id)
                self._cycle_index[desktop_id] = 0
                return
            next_index = current_index + 1
            self.activate_window(window=windows[next_index])
            self._cycle_index[desktop_id] = next_index
            return

        self._cycle_index[desktop_id] = 0
        self.activate_window(window=windows[0])

    def _minimize_windows(self, desktop_id: str) -> None:
        """Minimize all windows belonging to a desktop_id."""
        for w in self._get_windows_for(desktop_id=desktop_id):
            try:
                if not w.is_minimized():
                    w.minimize()
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="minimize", desktop_id=desktop_id).warning(
                    f"Failed to minimize window: {exc}"
                )

    def close_focused(self, desktop_id: str) -> ActionResult:
        """Close the active window only if it belongs to the desktop_id."""
        if self._screen is None:
            return ActionResult.UNSUPPORTED
        active_window = self._screen.get_active_window()
        if active_window is None:
            return ActionResult.NOT_FOUND
        active_xid = self._xid_for(window=active_window)
        if active_xid not in [
            self._xid_for(window=window)
            for window in self._get_windows_for(desktop_id=desktop_id)
        ]:
            return ActionResult.NOT_FOUND
        timestamp = Gtk.get_current_event_time() or 0
        try:
            active_window.close(timestamp)
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="close_focused", desktop_id=desktop_id).warning(
                f"Failed to close window: {exc}"
            )
            return ActionResult.FAILED
        return ActionResult.OK

    def close_all(self, desktop_id: str) -> ActionResult:
        """Close all windows for a desktop_id."""
        timestamp = Gtk.get_current_event_time() or 0
        windows = self._get_windows_for(desktop_id=desktop_id)
        if not windows:
            return ActionResult.NOT_FOUND
        result = ActionResult.OK
        for w in windows:
            try:
                w.close(timestamp)
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="close_all", desktop_id=desktop_id).warning(
                    f"Failed to close window: {exc}"
                )
                result = ActionResult.FAILED
        return result

    def close(self, window_id: WindowId) -> ActionResult:
        """Close one X11 window by backend-neutral window ID."""
        xid = self._xid_from_window_id(window_id)
        if xid is None:
            return ActionResult.UNSUPPORTED
        window = self._window_for_xid(xid=xid)
        if window is None:
            return ActionResult.NOT_FOUND
        timestamp = Gtk.get_current_event_time() or 0
        try:
            window.close(timestamp)
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="close_window", xid=str(xid)).warning(
                f"Failed to close window: {exc}"
            )
            return ActionResult.FAILED
        return ActionResult.OK

    def _ordered_windows_for(self, desktop_id: str) -> list[Wnck.Window]:
        """Return windows in a stable per-app cycle order."""
        windows = self._get_windows_for(desktop_id=desktop_id)
        if not windows:
            self._cycle_order_by_desktop.pop(desktop_id, None)
            self._cycle_index.pop(desktop_id, None)
            return []

        windows_by_xid: dict[int, Wnck.Window] = {}
        current_xids: list[int] = []
        for window in windows:
            xid = self._xid_for(window=window)
            windows_by_xid[xid] = window
            current_xids.append(xid)

        previous_order = self._cycle_order_by_desktop.get(desktop_id, [])
        membership_changed = len(previous_order) != len(current_xids) or set(
            previous_order
        ) != set(current_xids)
        ordered_xids = [xid for xid in previous_order if xid in windows_by_xid]
        ordered_xids.extend(xid for xid in current_xids if xid not in ordered_xids)
        self._cycle_order_by_desktop[desktop_id] = ordered_xids

        if membership_changed or self._cycle_index.get(desktop_id, 0) >= len(
            ordered_xids
        ):
            self._cycle_index[desktop_id] = 0

        return [windows_by_xid[xid] for xid in ordered_xids]

    def _xid_for(self, window: Wnck.Window | None) -> int:
        """Return the live XID for a window or 0 when unavailable."""
        if window is None:
            return 0
        try:
            return int(window.get_xid())
        except _RECOVERABLE_ERRORS as exc:
            log.bind(action="window_xid_lookup").warning(
                f"Failed to read window xid: {exc}"
            )
            return 0

    @staticmethod
    def _xid_from_window_id(window_id: WindowId) -> int | None:
        if window_id.backend is not DisplayServer.X11:
            return None
        try:
            return int(window_id.value)
        except (TypeError, ValueError):
            return None

    def _get_windows_for(self, desktop_id: str) -> list[Wnck.Window]:
        """Get windows for desktop_id using cached XIDs from last scan.

        This avoids rematching WM_CLASS in hover/preview paths, which can
        race with native Wnck object lifetime transitions.
        """
        if self._screen is None:
            return []

        by_xid: dict[int, Wnck.Window] = {}
        for window in self._iter_tasklist_windows():
            # Keep this path stricter than _xid_for(): the old implementation
            # mapped whatever XID Wnck returned and only skipped exceptions.
            # That preserves exact lookup behavior for cached XIDs, including
            # unusual but technically possible XID values.
            try:
                by_xid[int(window.get_xid())] = window
            except _RECOVERABLE_ERRORS as exc:
                log.bind(action="get_windows_xid").warning(
                    f"Skipping window while collecting xid map: {exc}"
                )
                continue

        result: list[Wnck.Window] = []
        active_ws = (
            self._screen.get_active_workspace()
            if self._config.current_workspace_only
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
