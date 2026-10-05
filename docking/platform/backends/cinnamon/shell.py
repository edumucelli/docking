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
)
from docking.platform.backends.reduced.services import ReducedSurfaceService

log = get_logger(name="backend.cinnamon.shell")

WINDOWS_SCRIPT = """
(() => {
    const generation = global._dockingWindowGeneration ||=
        imports.gi.GLib.uuid_string_random();
    const Meta = imports.gi.Meta;
    // Older Muffin may focus our native GTK toplevel on pointer press despite
    // GTK's dock hints. Keep toggle/minimize tied to the last eligible app.
    const focus = global.display.focus_window;
    const active = focus?.get_client_pid?.() === __DOCKING_PID__
        ? global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null)
            .find(w => w.get_client_pid?.() !== __DOCKING_PID__ &&
                       !w.is_skip_taskbar() && !w.minimized)
        : focus;
    return global.get_window_actors().map(a => a.meta_window)
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
                'frame-rect': [r.x, r.y, r.width, r.height]
            };
        });
})()
"""


class CinnamonShellClient:
    """Use the shell's existing API without installing a Cinnamon extension."""

    def __init__(self, *, proxy: Gio.DBusProxy) -> None:
        self._proxy = proxy
        self.last_query_failed = False

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
            if client._eval("typeof global.get_window_actors === 'function'") is True:
                return client
        except Exception as exc:
            log.info("Cinnamon shell positioning unavailable: %s", exc)
        return None

    def _eval(self, script: str) -> object:
        try:
            success, value = self._proxy.call_sync(
                "Eval",
                GLib.Variant("(s)", (script,)),
                Gio.DBusCallFlags.NO_AUTO_START,
                250,
                None,
            ).unpack()
            return json.loads(value) if success else None
        except Exception as exc:
            log.debug("Cinnamon shell request failed: %s", exc)
            return None

    def list_windows(self) -> Sequence[Mapping[str, Any]]:
        result = self._eval(WINDOWS_SCRIPT.replace("__DOCKING_PID__", str(os.getpid())))
        self.last_query_failed = not isinstance(result, list)
        if not isinstance(result, list):
            return ()
        return tuple(row for row in result if isinstance(row, Mapping))

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
            f".find(w => w.get_title() === {json.dumps(title)});"
            "if (!w) return null;"
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


class CinnamonShellSurfaceService(ReducedSurfaceService):
    """Position the main dock through Muffin while retaining limited capabilities."""

    def __init__(self, *, client: CinnamonShellClient) -> None:
        super().__init__()
        self._client = client
        self._title = f"Docking [{uuid4().hex}]"
        self._current_workspace_only = False
        self._position: tuple[int, int] | None = None
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
        if self._retry_source:
            GLib.source_remove(self._retry_source)
            self._retry_source = 0
        self._attempts_left = 0
        self._request = None
        self._position = None
        super().stop()
