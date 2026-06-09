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

"""AT-SPI (Assistive Technology Service Provider Interface) window service.

KWin 6 / Plasma 6 does not expose a public Wayland protocol for window
listing.  However, the accessibility bus (AT-SPI2) is always active on
modern Linux desktops and exposes every application's top-level windows
as accessible objects.  This module connects to the AT-SPI D-Bus bus,
enumerates accessible applications and their window/frame children, and
maps them into Docking's :class:`WindowService` contract.

AT-SPI is used by screen readers (Orca) and UI automation tools
(Dogtail, Accerciser).  It is a stable, cross-desktop mechanism that
works on KDE, GNOME, and other Wayland compositors.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence
from threading import Lock, Thread

from gi.repository import Gio, GLib

from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    Rect,
    WindowId,
    WindowService,
    WindowSnapshot,
)
from docking.platform.backends.wayland.toplevels import WaylandAppIdMatcher
from docking.platform.running import RunningAppInfo, RunningWindowInfo

log = get_logger(name="atspi_window")

# ---------------------------------------------------------------------------
# AT-SPI role names for window-like objects
# ---------------------------------------------------------------------------

_WINDOW_ROLES = frozenset({"window", "frame", "dialog", "application"})

# AT-SPI role IDs → names (commonly used subset)
_ROLE_BY_ID: dict[int, str] = {
    7: "application",
    26: "dialog",
    33: "frame",
    80: "window",
}

# AT-SPI state constants
_STATE_ACTIVE = 0
_STATE_FOCUSED = 3

# Path of the AT-SPI accessibility bus socket
_AT_SPI_BUS_PATH = "/run/user/{uid}/at-spi/bus_0"


def _at_spi_address() -> str:
    """Return the AT-SPI D-Bus address for the current session."""
    uid = os.getuid()
    return f"unix:path={_AT_SPI_BUS_PATH.format(uid=uid)}"


# ---------------------------------------------------------------------------
# Window data
# ---------------------------------------------------------------------------


class _AtspiWindow:
    """Mutable snapshot of one AT-SPI accessible window."""

    __slots__ = (
        "active",
        "app_id",
        "app_name",
        "fullscreen",
        "height",
        "minimized",
        "pid",
        "title",
        "width",
        "window_id",
        "x",
        "y",
    )

    def __init__(self, internal_id: str) -> None:
        self.window_id = WindowId(backend=DisplayServer.WAYLAND, value=internal_id)
        self.title: str = ""
        self.app_name: str = ""
        self.app_id: str | None = None
        self.active: bool = False
        self.minimized: bool = False
        self.fullscreen: bool = False
        self.x: int = 0
        self.y: int = 0
        self.width: int = 0
        self.height: int = 0
        self.pid: int = 0


# ---------------------------------------------------------------------------
# Window Service
# ---------------------------------------------------------------------------


class AtspiWindowService(WindowService):
    """WindowService backed by AT-SPI accessibility bus enumeration.

    Connects to the session's AT-SPI D-Bus bus and walks the
    accessibility tree of each running application to discover
    top-level windows (frames, windows, dialogs).

    Window actions (activate, close, minimize) are not available
    through AT-SPI.  They return :attr:`ActionResult.UNSUPPORTED`.
    """

    def __init__(
        self, *, launcher: object | None = None, model: object | None = None
    ) -> None:
        self._connection: Gio.DBusConnection | None = None
        self._windows: dict[str, _AtspiWindow] = {}
        self._lock = Lock()
        self._refresh_running = False
        self._model = model
        self._matcher: WaylandAppIdMatcher | None = None
        if launcher is not None:
            self._matcher = WaylandAppIdMatcher(launcher=launcher)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    # Set of window titles that indicate system/utility apps (not user windows)
    _SYSTEM_APPS = frozenset({
        "ksmserver", "kaccess", "gmenudbusmenuproxy", "xembedsniproxy",
        "evolution-alarm-notify", "xdg-desktop-portal-gtk",
        "polkit-kde-authentication-agent-1",
    })

    # Refresh interval in milliseconds
    _REFRESH_INTERVAL_MS = 5000

    def start(self) -> None:
        """Connect to the AT-SPI bus and perform an initial enumeration."""
        addr = _at_spi_address()
        try:
            conn = Gio.DBusConnection.new_for_address_sync(
                addr,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT,
                None,
                None,
            )
            # AT-SPI bus requires an explicit Hello
            conn.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "Hello",
                None,
                GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            self._connection = conn
            # Initial refresh in a background thread so the main loop
            # stays responsive during startup.
            self._schedule_refresh()
            # Start periodic refresh (runs in background thread)
            self._refresh_source_id = GLib.timeout_add(
                self._REFRESH_INTERVAL_MS, self._on_refresh_timer,
            )
            log.info("AT-SPI window service: connected")
        except Exception:
            log.exception("AT-SPI window service: failed to connect")
            self._connection = None
            self._refresh_source_id: int = 0

    def stop(self) -> None:
        sid = getattr(self, '_refresh_source_id', 0)
        if sid:
            GLib.source_remove(sid)
            self._refresh_source_id = 0
        if self._connection is not None:
            with contextlib.suppress(Exception):
                self._connection.close_sync(None)
            self._connection = None
        with self._lock:
            self._windows.clear()

    # ------------------------------------------------------------------
    # WindowService implementation
    # ------------------------------------------------------------------

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        snapshots = []
        with self._lock:
            for w in self._windows.values():
                snap = self._to_snapshot(w)
                if snap.desktop_id == desktop_id:
                    snapshots.append(snap)
        return snapshots

    def list_preview_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return self.list_windows(desktop_id)

    def icon_name_for_desktop(self, desktop_id: str) -> str:
        for w in self._windows.values():
            snap = self._to_snapshot(w)
            if snap.desktop_id == desktop_id and w.app_name:
                return w.app_name.lower()
        return ""

    def activate(self, window_id: WindowId) -> ActionResult:
        return ActionResult.UNSUPPORTED

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        return ActionResult.UNSUPPORTED

    def cycle(self, desktop_id: str) -> ActionResult:
        return ActionResult.UNSUPPORTED

    def minimize_all(self, desktop_id: str) -> ActionResult:
        return ActionResult.UNSUPPORTED

    def close(self, window_id: WindowId) -> ActionResult:
        return ActionResult.UNSUPPORTED

    def close_all(self, desktop_id: str) -> ActionResult:
        return ActionResult.UNSUPPORTED

    def close_focused(self, desktop_id: str) -> ActionResult:
        return ActionResult.UNSUPPORTED

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        return ActionResult.UNSUPPORTED

    # ------------------------------------------------------------------
    # Internal: AT-SPI enumeration
    # ------------------------------------------------------------------

    def _on_refresh_timer(self) -> bool:
        """GLib timer callback — runs refresh in a background thread."""
        self._schedule_refresh()
        return True  # keep timer running

    def _schedule_refresh(self) -> None:
        """Run _refresh in a background thread, skipping if one is already in flight."""
        if self._refresh_running:
            return
        thread = Thread(target=self._refresh, daemon=True)
        thread.start()

    def _refresh(self) -> None:
        """Re-enumerate all windows from the AT-SPI bus."""
        conn = self._connection
        if conn is None:
            return

        self._refresh_running = True
        try:
            new_windows: dict[str, _AtspiWindow] = {}

            # 1. List all services on the AT-SPI bus
            try:
                result = conn.call_sync(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "ListNames",
                    None,
                    GLib.VariantType("(as)"),
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
                )
                names = result.get_child_value(0).unpack()  # list[str]
            except Exception:
                log.exception("AT-SPI: ListNames failed")
                return

            # 2. For each unique-name connection, enumerate accessible windows
            for svc in names:
                if not svc.startswith(":") or svc == ":1.0":
                    continue
                with contextlib.suppress(Exception):
                    self._enumerate_service(conn, svc, new_windows)

            with self._lock:
                self._windows = new_windows

            # Publish running windows to the model so indicators appear
            self._publish_running()
        finally:
            self._refresh_running = False

    def _publish_running(self) -> None:
        """Build RunningAppInfo per desktop_id and push to the model."""
        model = self._model
        if model is None:
            return
        matcher = self._matcher

        # Sync visible items into the matcher
        if matcher is not None:
            with contextlib.suppress(Exception):
                matcher.sync_visible_items(model.visible_items())

        # Group windows by resolved desktop_id
        by_desktop: dict[str, list[_AtspiWindow]] = {}
        with self._lock:
            for w in self._windows.values():
                desktop_id = None
                if matcher is not None and w.app_name:
                    desktop_id = matcher.match(w.app_name)
                if not desktop_id:
                    desktop_id = f"kwin:{w.app_name or w.window_id.value}"
                by_desktop.setdefault(desktop_id, []).append(w)

        # Build RunningAppInfo per desktop_id
        running: dict[str, RunningAppInfo] = {}
        for desktop_id, windows in by_desktop.items():
            rwis = []
            for w in windows:
                rwis.append(RunningWindowInfo(
                    desktop_id=desktop_id,
                    xid=0,
                    window_id=w.window_id,
                    active=w.active,
                    urgent=False,
                    window=None,
                ))
            running[desktop_id] = RunningAppInfo.from_windows(rwis)

        try:
            model.update_running(running=running)
        except Exception:
            log.exception("AT-SPI: update_running failed")

    def _enumerate_service(
        self,
        conn: Gio.DBusConnection,
        service: str,
        out: dict[str, _AtspiWindow],
    ) -> None:
        """Walk the accessible tree of *service* and collect windows."""
        root = "/org/a11y/atspi/accessible/root"

        app_name = self._get_prop(conn, service, root, "Name") or service

        # Get children of the application root
        try:
            r = conn.call_sync(
                service,
                root,
                "org.a11y.atspi.Accessible",
                "GetChildren",
                None,
                GLib.VariantType("(a(so))"),
                Gio.DBusCallFlags.NONE,
                500,
                None,
            )
            children = r.get_child_value(0)
            n = children.n_children()
        except Exception:
            return

        for i in range(n):
            child = children.get_child_value(i)
            c_svc = child.get_child_value(0).get_string()
            c_path = child.get_child_value(1).get_string()

            role = self._get_role_name(conn, c_svc, c_path)

            if role in _WINDOW_ROLES:
                self._collect_window(conn, c_svc, c_path, role, app_name, out)
            elif role == "application":
                # Walk one level deeper: application → windows
                self._walk_children(conn, c_svc, c_path, app_name, out, depth=1)
            else:
                # Walk deeper in case windows are nested
                self._walk_children(conn, c_svc, c_path, app_name, out, depth=1)

    def _walk_children(
        self,
        conn: Gio.DBusConnection,
        service: str,
        path: str,
        app_name: str,
        out: dict[str, _AtspiWindow],
        depth: int = 0,
    ) -> None:
        """Recurse into children up to *depth* looking for window objects."""
        if depth > 2:
            return

        try:
            r = conn.call_sync(
                service,
                path,
                "org.a11y.atspi.Accessible",
                "GetChildren",
                None,
                GLib.VariantType("(a(so))"),
                Gio.DBusCallFlags.NONE,
                500,
                None,
            )
            children = r.get_child_value(0)
            n = children.n_children()
        except Exception:
            return

        for i in range(n):
            child = children.get_child_value(i)
            c_svc = child.get_child_value(0).get_string()
            c_path = child.get_child_value(1).get_string()

            role = self._get_role_name(conn, c_svc, c_path)
            if role in _WINDOW_ROLES:
                self._collect_window(conn, c_svc, c_path, role, app_name, out)
            elif depth < 2:
                self._walk_children(conn, c_svc, c_path, app_name, out, depth + 1)

    def _collect_window(
        self,
        conn: Gio.DBusConnection,
        service: str,
        path: str,
        role: str,
        app_name: str,
        out: dict[str, _AtspiWindow],
    ) -> None:
        """Extract window properties from an accessible object."""

        # Skip system/utility applications
        if app_name in self._SYSTEM_APPS:
            return

        internal_id = f"{service}{path}"
        w = _AtspiWindow(internal_id)

        # Window title
        name = self._get_prop(conn, service, path, "Name")
        w.title = name or ""

        # App identification
        w.app_name = app_name
        w.app_id = app_name

        # Active / focused state
        state = self._get_prop(conn, service, path, "GetState")
        if isinstance(state, list):
            w.active = _STATE_ACTIVE in state or _STATE_FOCUSED in state

        # Attributes may carry PID, WM_CLASS, etc.
        attrs = self._get_prop(conn, service, path, "GetAttributes")
        if isinstance(attrs, dict):
            pid_str = attrs.get("process-id", "")
            if pid_str:
                with contextlib.suppress(ValueError, TypeError):
                    w.pid = int(pid_str)
            # Use toolkit name as additional app_id disambiguation
            toolkit = attrs.get("toolkit", "")
            if toolkit and w.app_id:
                w.app_id = f"{w.app_id}.{toolkit}"

        # Geometry via Component interface — only if the object
        # actually implements it (avoids impl_GetExtents CRITICALs).
        if self._has_interface(conn, service, path, "org.a11y.atspi.Component"):
            try:
                gr = conn.call_sync(
                    service,
                    path,
                    "org.a11y.atspi.Component",
                    "GetExtents",
                    GLib.Variant("(u)", (0,)),  # 0 = ATSPI_COORD_TYPE_SCREEN
                    GLib.VariantType("(iiii)"),
                    Gio.DBusCallFlags.NONE,
                    500,
                    None,
                )
                w.x, w.y, w.width, w.height = gr.get_child_value(0).unpack()
            except Exception:
                pass

        # Avoid duplicates: prefer the first entry for each internal_id
        if internal_id not in out:
            out[internal_id] = w

    # ------------------------------------------------------------------
    # D-Bus helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_interface(
        conn: Gio.DBusConnection,
        service: str,
        path: str,
        iface_name: str,
    ) -> bool:
        """Check whether an accessible object implements *iface_name*."""
        try:
            r = conn.call_sync(
                service,
                path,
                "org.a11y.atspi.Accessible",
                "GetInterfaces",
                None,
                GLib.VariantType("(as)"),
                Gio.DBusCallFlags.NONE,
                500,
                None,
            )
            ifaces = r.get_child_value(0).unpack()
            return iface_name in ifaces
        except Exception:
            return False

    @staticmethod
    def _get_prop(
        conn: Gio.DBusConnection,
        service: str,
        path: str,
        prop_name: str,
    ) -> object | None:
        """Read an AT-SPI Accessible property, returning a Python value."""
        try:
            r = conn.call_sync(
                service,
                path,
                "org.freedesktop.DBus.Properties",
                "Get",
                GLib.Variant("(ss)", ("org.a11y.atspi.Accessible", prop_name)),
                GLib.VariantType("(v)"),
                Gio.DBusCallFlags.NONE,
                500,
                None,
            )
            return r.get_child_value(0).unpack()
        except Exception:
            return None

    @staticmethod
    def _get_role_name(
        conn: Gio.DBusConnection,
        service: str,
        path: str,
    ) -> str:
        """Get the accessible role name, falling back to role ID lookup."""
        try:
            r = conn.call_sync(
                service,
                path,
                "org.a11y.atspi.Accessible",
                "GetRoleName",
                None,
                GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE,
                500,
                None,
            )
            return r.get_child_value(0).get_string()
        except Exception:
            pass
        try:
            r = conn.call_sync(
                service,
                path,
                "org.a11y.atspi.Accessible",
                "GetRole",
                None,
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                500,
                None,
            )
            role_id = r.get_child_value(0).get_uint32()
            return _ROLE_BY_ID.get(role_id, f"role_{role_id}")
        except Exception:
            return "?"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _to_snapshot(self, w: _AtspiWindow) -> WindowSnapshot:
        # Resolve to a proper .desktop ID via the matcher, falling back to
        # app_name with a kwin: prefix so the UI can still show something.
        desktop_id = None
        if self._matcher is not None and w.app_name:
            desktop_id = self._matcher.match(w.app_name)
        if not desktop_id:
            desktop_id = f"kwin:{w.app_name or w.window_id.value}"

        return WindowSnapshot(
            id=w.window_id,
            desktop_id=desktop_id,
            title=w.title or "Window",
            app_id=w.app_id,
            wm_class=w.app_name or None,
            active=w.active,
            minimized=w.minimized,
            fullscreen=w.fullscreen,
            geometry=Rect(
                x=w.x,
                y=w.y,
                width=w.width,
                height=w.height,
            ) if (w.width > 0 and w.height > 0) else None,
            can_activate=False,
            can_minimize=False,
            can_close=False,
            can_preview=False,
        )


