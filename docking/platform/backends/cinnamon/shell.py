"""Dock placement and window control through Cinnamon's built-in shell API.

Cinnamon 6.4/6.6 lack layer-shell. Native GTK movement is ignored there, but
org.Cinnamon.Eval can ask Muffin to move our own window. A unique window title
identifies the dock independently of client PID availability.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import uuid4

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from docking.core.position import Position
from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
    WindowId,
    WorkspaceSnapshot,
)
from docking.platform.backends.reduced.services import ReducedSurfaceService

log = get_logger(name="backend.cinnamon.shell")

NATIVE_FEATURES = frozenset(
    {"workspaces", "visibility", "previews", "desktop", "picking", "color"}
)
FEATURES_SCRIPT = """
(() => {
    const Meta = imports.gi.Meta, wm = global.workspace_manager;
    const supports = probe => {try {return !!probe();} catch (_) {return false;}};
    return {
        color: supports(() => typeof
               imports.gi.Cinnamon.Screenshot.prototype.pick_color === 'function' &&
               typeof Meta.CursorTracker.prototype.set_pointer_visible === 'function' &&
               typeof Meta.CursorTracker.prototype.get_pointer_visible === 'function'),
        windows: typeof global.get_window_actors === 'function',
        workspaces: !!wm && typeof wm.get_workspace_by_index === 'function',
        visibility: typeof Meta.Window.prototype.get_frame_rect === 'function' &&
                    typeof Meta.Window.prototype.showing_on_its_workspace ===
                    'function',
        previews: supports(() => typeof Meta.WindowActor.prototype.get_image ===
                  'function'),
        desktop: !!wm && typeof wm.toggle_desktop === 'function' &&
                 typeof wm.show_desktop === 'function' &&
                 typeof wm.unshow_desktop === 'function',
        picking: typeof global.stage.get_actor_at_pos === 'function' &&
                 typeof Meta.Window.prototype.kill === 'function' &&
                 typeof Meta.Window.prototype.get_client_pid === 'function'
    };
})()
"""

WINDOWS_SCRIPT = """
(() => {
    const generation = global._dockingWindowGeneration ||=
        imports.gi.GLib.uuid_string_random();
    const Meta = imports.gi.Meta;
    // Older Muffin may focus our native GTK toplevel on pointer press despite
    // GTK's dock hints. Keep toggle/minimize tied to the last eligible app.
    const focused = global.display.focus_window;
    const focus = focused?.get_workspace() ? focused : null;
    const active = focus?.get_client_pid?.() === __DOCKING_PID__
        ? global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null)
            .find(w => w.get_workspace() &&
                       w.get_client_pid?.() !== __DOCKING_PID__ &&
                       !w.is_skip_taskbar() && !w.minimized)
        : focus;
    return global.get_window_actors().map(a => a.meta_window)
        // Closing actors outlive their Wayland surface during animation.
        // Muffin's get_client_pid() dereferences that surface without a guard.
        .filter(w => w && w.get_workspace())
        .sort((a, b) => ((b.get_user_time() - a.get_user_time()) | 0) ||
                        b.get_stable_sequence() - a.get_stable_sequence())
        .map(w => {
            const read = (method, fallback) => {
                try { return typeof w[method] === 'function' ? w[method]() : fallback; }
                catch (_) { return fallback; }
            };
            const r = w.get_frame_rect();
            const workspace = w.get_workspace();
            const clientPid = read('get_client_pid', 0);
            const type = w.get_window_type();
            return {
                id: w.get_stable_sequence(), generation, title: w.get_title(),
                'wm-class': w.get_wm_class(),
                'wm-class-instance': w.get_wm_class_instance(),
                'gtk-application-id': read('get_gtk_application_id', ''),
                'sandboxed-app-id': read('get_sandboxed_app_id', ''),
                pid: clientPid > 0 ? clientPid : read('get_pid', -1),
                focused: w === active,
                'demands-attention': !!w.demands_attention || !!w.urgent,
                'skip-taskbar': w.is_skip_taskbar(),
                'desktop-or-dock': type === Meta.WindowType.DESKTOP ||
                                   type === Meta.WindowType.DOCK,
                minimized: !!w.minimized,
                maximized: !!w.maximized_horizontally && !!w.maximized_vertically,
                fullscreen: !!w.fullscreen,
                'can-minimize': read('can_minimize', true),
                'can-close': read('can_close', true),
                workspace: workspace ? workspace.index() : null,
                sticky: read('is_on_all_workspaces', false),
                visible: read('showing_on_its_workspace', true),
                dialog: type === Meta.WindowType.DIALOG ||
                        type === Meta.WindowType.MODAL_DIALOG,
                'frame-rect': [r.x, r.y, r.width, r.height]
            };
        });
})()
"""


class CinnamonShellClient:
    """Use the shell's existing API without installing a Cinnamon extension."""

    def __init__(self, *, proxy: Gio.DBusProxy) -> None:
        self._proxy = proxy
        self.features = NATIVE_FEATURES
        self.last_query_failed = False
        self.last_request_failed = False
        self.snapshot_revision = 0
        self.workspace_snapshots: tuple[WorkspaceSnapshot, ...] = ()
        self.rows: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def connect(cls) -> CinnamonShellClient | None:
        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                "org.Cinnamon",
                "/org/Cinnamon",
                "org.Cinnamon",
                None,
            )
            client = cls(proxy=proxy)
            features = client._eval(FEATURES_SCRIPT)
            if isinstance(features, dict) and features.get("windows") is True:
                client.features = frozenset(
                    name for name in NATIVE_FEATURES if features.get(name) is True
                )
                return client
        except Exception as exc:
            log.info("Cinnamon shell positioning unavailable: %s", exc)
        return None

    @property
    def bus_name(self) -> str | None:
        return self._proxy.get_connection().get_unique_name()

    def _eval(self, script: str) -> object:
        try:
            success, value = self._proxy.call_sync(
                "Eval",
                GLib.Variant("(s)", (script,)),
                Gio.DBusCallFlags.NO_AUTO_START,
                250,
                None,
            ).unpack()
            self.last_request_failed = not success
            return json.loads(value) if success else None
        except Exception as exc:
            self.last_request_failed = True
            log.debug("Cinnamon shell request failed: %s", exc)
            return None

    def list_windows(self) -> Sequence[Mapping[str, Any]]:
        windows = WINDOWS_SCRIPT.replace("__DOCKING_PID__", str(os.getpid()))
        result = self._eval(
            "(() => {const windows = " + windows + ";"
            "const wm = global.workspace_manager;"
            "return {windows, workspaces: wm ? Array.from({length:wm.n_workspaces},"
            " (_, i) => ({id:String(i), number:i,"
            "name:imports.ui.main.getWorkspaceName(i),"
            "active:i === wm.get_active_workspace_index()})) : []};})()"
        )
        rows = result.get("windows") if isinstance(result, dict) else result
        self.last_query_failed = not isinstance(rows, list)
        if not isinstance(rows, list):
            return ()
        if isinstance(result, dict):
            workspaces = result.get("workspaces", [])
            self.workspace_snapshots = tuple(
                WorkspaceSnapshot(
                    id=row["id"],
                    number=row["number"],
                    name=row.get("name", ""),
                    active=row.get("active") is True,
                )
                for row in workspaces
                if isinstance(row, dict)
                and isinstance(row.get("id"), str)
                and type(row.get("number")) is int
                and isinstance(row.get("name", ""), str)
            )
        self.rows = tuple(row for row in rows if isinstance(row, Mapping))
        self.snapshot_revision += 1
        return self.rows

    def activate_workspace(self, workspace_id: str) -> ActionResult:
        if not workspace_id.isascii() or not workspace_id.isdigit():
            return ActionResult.NOT_FOUND
        result = self._eval(
            "(() => {const wm = global.workspace_manager;"
            f"const w = wm.get_workspace_by_index({int(workspace_id)});"
            "if (!w) return 'not_found';"
            "w.activate(global.get_current_time()); return 'ok';})()"
        )
        return self._action_result(result)

    def show_desktop(self, show: bool | None) -> ActionResult:
        operation = (
            "toggle_desktop"
            if show is None
            else "show_desktop"
            if show
            else "unshow_desktop"
        )
        timestamp = "global.get_current_time()" if show is not False else ""
        return self._action_result(
            self._eval(f"global.workspace_manager.{operation}({timestamp}); 'ok'")
        )

    @staticmethod
    def _action_result(result: object) -> ActionResult:
        return next(
            (value for value in ActionResult if value.value == result),
            ActionResult.FAILED,
        )

    def for_window(self, window_id: WindowId, body: str) -> object:
        """Run a fixed operation only after resolving a live, foreign target."""
        if window_id.backend is not DisplayServer.WAYLAND:
            return None
        parts = str(window_id.value).split(":")
        if (
            len(parts) != 3
            or parts[0] != "cinnamon"
            or not parts[1]
            or not parts[2].isascii()
            or not parts[2].isdigit()
            or int(parts[2]) <= 0
        ):
            return None
        return self._eval(
            "(() => {"
            f"if (global._dockingWindowGeneration !== {json.dumps(parts[1])})"
            " return null;"
            "const w = global.get_window_actors().map(a => a.meta_window)"
            ".filter(w => w && w.get_workspace())"
            f".find(w => w.get_stable_sequence() === {int(parts[2])});"
            "if (!w || w.is_skip_taskbar() || "
            f"w.get_client_pid?.() === {os.getpid()}) return null;" + body + "})()"
        )

    def window_action(self, window_id: WindowId, action: str) -> ActionResult:
        """Resolve a live sequence in the same shell generation before acting."""
        scripts = {
            "activate": (
                "imports.ui.main.activateWindow(w, global.get_current_time(),"
                " w.get_workspace()?.index())"
            ),
            "minimize": (
                "if (typeof w.can_minimize === 'function' && !w.can_minimize())"
                " return 'unsupported'; w.minimize()"
            ),
            "close": (
                "if (typeof w.can_close === 'function' && !w.can_close())"
                " return 'unsupported'; w.delete(global.get_current_time())"
            ),
        }
        if action not in scripts:
            return ActionResult.UNSUPPORTED
        if window_id.backend is not DisplayServer.WAYLAND:
            return ActionResult.NOT_FOUND
        parts = str(window_id.value).split(":")
        if (
            len(parts) != 3
            or parts[0] != "cinnamon"
            or not parts[1]
            or not parts[2].isascii()
            or not parts[2].isdigit()
        ):
            return ActionResult.NOT_FOUND
        sequence = int(parts[2])
        if sequence <= 0:
            return ActionResult.NOT_FOUND
        result = self._eval(
            "(() => {"
            f"if (global._dockingWindowGeneration !== {json.dumps(parts[1])})"
            " return 'not_found';"
            "const w = global.get_window_actors().map(a => a.meta_window)"
            ".filter(w => w && w.get_workspace())"
            f".find(w => w.get_stable_sequence() === {sequence});"
            "if (!w || w.is_skip_taskbar() || "
            f"w.get_client_pid?.() === {os.getpid()}) return 'not_found';"
            + scripts[action]
            + "; return 'ok'; })()"
        )
        log.debug("Window %s action %s: %s", window_id, action, result)
        return next(
            (value for value in ActionResult if value.value == result),
            ActionResult.FAILED,
        )

    def workarea(
        self, monitor: MonitorSnapshot, *, exclude_title: str | None = None
    ) -> Rect | None:
        geometry = monitor.geometry
        wanted = json.dumps([geometry.x, geometry.y, geometry.width, geometry.height])
        # GDK's Wayland output order need not match Muffin's monitor indexes.
        result = self._eval(
            f"(() => {{ const wanted = {wanted}; let index = -1;"
            "for (let i = 0; i < global.display.get_n_monitors(); i++) {"
            "const g = global.display.get_monitor_geometry(i);"
            "if ([g.x, g.y, g.width, g.height].every((v, n) => v === wanted[n])) {"
            "index = i; break; }} if (index < 0) return null;"
            # Exclude our chrome actor while asking Muffin for external space.
            # Otherwise every relayout includes our own strut and walks inward.
            f"const owned = global._dockingReservations?.[{json.dumps(exclude_title)}];"
            "const chrome = imports.ui.main.layoutManager._chrome;"
            "if (owned) { chrome.modifyActorParams(owned.actor, {affectsStruts:false});"
            "chrome.updateRegions(); }"
            "try { const r = global.workspace_manager.get_active_workspace()"
            ".get_work_area_for_monitor(index); return [r.x, r.y, r.width, r.height]; }"
            "finally { if (owned) {"
            "chrome.modifyActorParams(owned.actor, {affectsStruts:true});"
            "chrome.updateRegions(); }} })()"
        )
        if (
            isinstance(result, list)
            and len(result) == 4
            and all(type(part) is int for part in result)
        ):
            values = cast(list[int], result)
            if values[2] > 0 and values[3] > 0:
                return Rect(*values)
        return None

    def reserve_dock(self, *, title: str, request: ReservationRequest) -> bool:
        """Use Cinnamon's chrome struts, which Muffin honors for native clients.

        The transparent, non-input actor reserves only the resting shelf (plus
        gap/panel offset), not the much larger animation surface. Its owner
        window's unmanaged signal removes it even if Docking crashes.
        """
        g = request.monitor.geometry
        extent = max(0, request.thickness + request.edge_offset)
        if request.position in (Position.TOP, Position.BOTTOM):
            extent = min(extent, g.height)
            rect = [
                g.x,
                g.y if request.position == Position.TOP else g.y + g.height - extent,
                g.width,
                extent,
            ]
        elif request.position in (Position.LEFT, Position.RIGHT):
            extent = min(extent, g.width)
            rect = [
                g.x if request.position == Position.LEFT else g.x + g.width - extent,
                g.y,
                extent,
                g.height,
            ]
        else:
            return False
        return (
            self._eval(
                "(() => { const key = " + json.dumps(title) + ";"
                "const w = global.get_window_actors().map(a => a.meta_window)"
                ".find(w => w.get_title() === key); if (!w) return false;"
                "const reservations = global._dockingReservations ||= {};"
                "let owned = reservations[key]; if (!owned) {"
                "const actor = new imports.gi.Clutter.Actor({"
                "opacity:0, reactive:false});"
                "imports.ui.main.layoutManager.addChrome(actor, {"
                "affectsStruts:true, affectsInputRegion:false,"
                "visibleInFullscreen:true});"
                "owned = {actor, window:w}; reservations[key] = owned;"
                "owned.signal = w.connect('unmanaged', () => {"
                "actor.destroy(); delete reservations[key]; }); }"
                f"const r = {json.dumps(rect)};"
                "owned.actor.set_position(r[0], r[1]);"
                "owned.actor.set_size(r[2], r[3]);"
                "return true; })()"
            )
            is True
        )

    def clear_reservation(self, *, title: str) -> None:
        self._eval(
            "(() => { const key = " + json.dumps(title) + ";"
            "const owned = global._dockingReservations?.[key];"
            "if (owned) { owned.window.disconnect(owned.signal);"
            "owned.actor.destroy(); delete global._dockingReservations[key]; }"
            "return true; })()"
        )

    def position_dock(
        self,
        *,
        title: str,
        request: PlacementRequest,
        current_workspace_only: bool,
    ) -> tuple[int, int] | None:
        # JSON encoding keeps window identifiers separate from JavaScript code.
        result = self._eval(
            "(() => { const w = global.get_window_actors()"
            ".map(a => a.meta_window)"
            ".filter(w => w && w.get_workspace())"
            f".find(w => w.get_title() === {json.dumps(title)});"
            "if (!w) return null;"
            # Older Muffin treats native GTK docks as ordinary toplevels.
            # Keep only our actor reachable during Show Desktop; do not alter
            # native visibility/minimize state of other application windows.
            "const guards = global._dockingDockActors ||= {};"
            f"const key = {json.dumps(title)};"
            "if (!guards[key]) {const actor = w.get_compositor_private();"
            "if (actor) {const visible = actor.connect('notify::visible', () => {"
            "if (!actor.visible && w.get_workspace() && !w.minimized &&"
            "w.located_on_workspace(global.workspace_manager.get_active_workspace()))"
            " actor.show(); });"
            "const unmanaged = w.connect('unmanaged', () => {"
            "actor.disconnect(visible); delete guards[key]; });"
            "guards[key] = {actor, window:w, visible, unmanaged}; }}"
            f"w.{'unstick' if current_workspace_only else 'stick'}();"
            f"w.{'make_above' if request.keep_above else 'unmake_above'}();"
            # A shell/user move may occupy the reserved strip; an application
            # move is constrained into its own newly reduced workarea by Muffin.
            "w.move_resize_frame(true, "
            f"{int(request.x)}, {int(request.y)}, "
            f"{int(request.size.width)}, {int(request.size.height)});"
            "const r = w.get_frame_rect(); return [r.x, r.y]; })()"
        )
        if (
            isinstance(result, list)
            and len(result) == 2
            and all(type(part) is int for part in result)
        ):
            values = cast(list[int], result)
            return values[0], values[1]
        return None

    def clear_dock_visibility(self, *, title: str) -> None:
        self._eval(
            "(() => {const guards = global._dockingDockActors;"
            f"const key = {json.dumps(title)}, owned = guards?.[key];"
            "if (owned) {owned.actor.disconnect(owned.visible);"
            "owned.window.disconnect(owned.unmanaged); delete guards[key];}"
            "return true;})()"
        )


class CinnamonShellSurfaceService(ReducedSurfaceService):
    """Position the main dock through Muffin while retaining limited capabilities."""

    def __init__(self, *, client: CinnamonShellClient) -> None:
        super().__init__()
        self._client = client
        self._title = f"Docking [{uuid4().hex}]"
        self._current_workspace_only = False
        self._position: tuple[int, int] | None = None
        self._position_revision = 0
        self._request: PlacementRequest | None = None
        self._retry_source = 0
        self._attempts_left = 0
        self._reservation: ReservationRequest | None = None

    @property
    def popups_use_parent_relative_coordinates(self) -> bool:
        return True

    def configure_before_realize(self, window: object) -> None:
        super().configure_before_realize(window)
        set_title = getattr(window, "set_title", None)
        if callable(set_title):
            set_title(self._title)

    def set_workspace_scope(self, *, current_workspace_only: bool) -> None:
        self._current_workspace_only = current_workspace_only
        if self._request is not None:
            self.position_or_anchor(self._request)

    def external_workarea(self, monitor: MonitorSnapshot) -> Rect | None:
        return self._client.workarea(monitor, exclude_title=self._title)

    def set_reservation(self, request: ReservationRequest) -> None:
        self._reservation = request
        self._client.reserve_dock(title=self._title, request=request)

    def clear_reservation(self) -> None:
        if self._reservation is not None:
            self._client.clear_reservation(title=self._title)
            self._reservation = None

    def get_surface_position(self) -> tuple[int, int] | None:
        # Muffin can adjust the frame again after an asynchronous GTK resize.
        # Reuse the shared native snapshot instead of another geometry query.
        if (
            self._request is not None
            and not self._client.last_query_failed
            and self._client.snapshot_revision > self._position_revision
        ):
            for row in self._client.rows:
                rect = row.get("frame-rect")
                if (
                    row.get("title") == self._title
                    and isinstance(rect, list)
                    and len(rect) == 4
                    and all(type(v) is int for v in rect)
                ):
                    self._position = rect[0], rect[1]
                    self._position_revision = self._client.snapshot_revision
                    break
        return self._position

    def position_or_anchor(self, request: PlacementRequest) -> None:
        window = self._window
        if window is None:
            return
        for method_name in ("set_size_request", "resize"):
            method = getattr(window, method_name, None)
            if callable(method):
                method(request.size.width, request.size.height)
        self._request = request
        # The GTK realize callback precedes Muffin seeing the mapped window.
        # Retry across mapping and asynchronous Wayland resize/configure events.
        self._attempts_left = 20
        self._apply_position()
        if not self._retry_source:
            self._retry_source = GLib.timeout_add(100, self._retry_position)

    def _apply_position(self) -> None:
        if self._request is None:
            return
        position = self._client.position_dock(
            title=self._title,
            request=self._request,
            current_workspace_only=self._current_workspace_only,
        )
        if position is not None:
            self._position = position
            self._position_revision = self._client.snapshot_revision
            if self._reservation is not None:
                self._client.reserve_dock(title=self._title, request=self._reservation)

    def _retry_position(self) -> bool:
        self._attempts_left -= 1
        self._apply_position()
        if self._attempts_left > 0:
            return True
        self._retry_source = 0
        if self._position is None:
            log.warning("Cinnamon could not position the dock window")
        return False

    def stop(self) -> None:
        self.clear_reservation()
        self._client.clear_dock_visibility(title=self._title)
        if self._retry_source:
            GLib.source_remove(self._retry_source)
            self._retry_source = 0
        self._attempts_left = 0
        self._request = None
        self._position = None
        super().stop()
