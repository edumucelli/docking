"""Public KWin scripting bridge, scoped to scripts owned by this client."""

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Gio, GLib

from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    DesktopActionService,
    DisplayServer,
    Rect,
    WindowId,
    WindowSnapshot,
)
from docking.platform.backends.native_windows import NativeWindowService

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel

log = get_logger(name="kwin_bridge")
_XML = """<node><interface name="org.docking.KWinBridge">
<method name="Publish"><arg type="s" direction="in"/></method>
<method name="ActionResult"><arg type="s" direction="in"/>
<arg type="b" direction="in"/></method>
</interface></node>"""


class KWinBridgeClient:
    def __init__(self, *, bus: Gio.DBusConnection, proxy: Gio.DBusProxy) -> None:
        self._bus = bus
        self._proxy = proxy
        self._owner = proxy.get_name_owner()
        self._path = f"/org/docking/KWinBridge/b{uuid.uuid4().hex}"
        self._plugin = f"docking-native-{uuid.uuid4().hex}"
        self._directory = tempfile.TemporaryDirectory(prefix="docking-kwin-")
        self._registration = 0
        self._loaded: set[str] = set()
        self._windows: tuple[WindowSnapshot, ...] = ()
        self._callbacks: dict[object, Callable[[], None]] = {}
        self._actions: dict[str, bool | None] = {}
        self._received = False
        self._stopped = False
        self._owner_watch = 0
        self._signal_watch = 0
        cached = proxy.get_cached_property("showingDesktop")
        self._showing_desktop = cached.unpack() is True if cached is not None else False

    @classmethod
    def connect(cls) -> KWinBridgeClient | None:
        client = None
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                "org.kde.KWin",
                "/KWin",
                "org.kde.KWin",
                None,
            )
            if not proxy.get_name_owner():
                return None
            client = cls(bus=bus, proxy=proxy)
            interface = Gio.DBusNodeInfo.new_for_xml(_XML).interfaces[0]
            client._registration = bus.register_object(
                client._path, interface, client._method, None, None
            )
            source = Path(__file__).with_name("bridge.js").read_text(encoding="utf-8")
            for marker, value in (
                ("__DESTINATION__", bus.get_unique_name()),
                ("__PATH__", client._path),
                ("__PLUGIN__", client._plugin),
                ("__PID__", os.getpid()),
            ):
                source = source.replace(marker, json.dumps(value))
            client._load(source, client._plugin)
            if not client._wait(lambda: client._received, 1500):
                raise RuntimeError("KWin did not publish a native snapshot")
            client._owner_watch = proxy.connect(
                "notify::g-name-owner", client._owner_changed
            )
            client._signal_watch = proxy.connect("g-signal", client._signal)
            return client
        except (GLib.Error, OSError, RuntimeError) as exc:
            log.info("KWin native bridge unavailable, retaining AT-SPI: %s", exc)
            if client is not None:
                client.stop()
            return None

    def _call(
        self,
        path: str,
        interface: str,
        method: str,
        parameters: GLib.Variant,
        reply: str | None = None,
    ) -> GLib.Variant:
        return self._bus.call_sync(
            "org.kde.KWin",
            path,
            interface,
            method,
            parameters,
            GLib.VariantType.new(reply) if reply else None,
            Gio.DBusCallFlags.NO_AUTO_START,
            500,
            None,
        )

    def _load(self, source: str, plugin: str) -> None:
        path = Path(self._directory.name) / f"{plugin}.js"
        path.write_text(source, encoding="utf-8")
        script_id = self._call(
            "/Scripting",
            "org.kde.kwin.Scripting",
            "loadScript",
            GLib.Variant("(ss)", (str(path), plugin)),
            "(i)",
        ).unpack()[0]
        if script_id < 0:
            raise RuntimeError("KWin refused the owned script")
        self._loaded.add(plugin)
        self._call(
            f"/Scripting/Script{script_id}",
            "org.kde.kwin.Script",
            "run",
            GLib.Variant("()", ()),
        )

    def _unload(self, plugin: str) -> None:
        if plugin not in self._loaded:
            return
        self._loaded.discard(plugin)
        with suppress(GLib.Error):
            self._call(
                "/Scripting",
                "org.kde.kwin.Scripting",
                "unloadScript",
                GLib.Variant("(s)", (plugin,)),
            )

    @staticmethod
    def _wait(predicate: Callable[[], bool], timeout_ms: int) -> bool:
        loop = GLib.MainLoop()

        def check() -> bool:
            if predicate():
                loop.quit()
                return False
            return True

        def timeout() -> bool:
            loop.quit()
            return False

        poll = GLib.timeout_add(5, check)
        deadline = GLib.timeout_add(timeout_ms, timeout)
        loop.run()
        for source in (poll, deadline):
            if GLib.MainContext.default().find_source_by_id(source) is not None:
                GLib.source_remove(source)
        return predicate()

    def _method(
        self, _connection, sender, _path, _interface, method, parameters, invocation
    ) -> None:
        if sender != self._owner:
            invocation.return_dbus_error(
                "org.docking.KWinBridge.Denied", "Not the current compositor"
            )
            return
        args = parameters.unpack()
        if method == "Publish":
            try:
                self._windows = snapshots_from_json(args[0])
            except (ValueError, TypeError):
                invocation.return_dbus_error(
                    "org.docking.KWinBridge.Invalid", "Invalid window snapshot"
                )
                return
            self._received = True
            for callback in tuple(self._callbacks.values()):
                callback()
        elif method == "ActionResult" and args[0] in self._actions:
            self._actions[args[0]] = args[1]
        invocation.return_value(GLib.Variant("()", ()))

    def _owner_changed(self, _proxy, _property) -> None:
        if self._proxy.get_name_owner() != self._owner:
            self._windows = ()
            for callback in tuple(self._callbacks.values()):
                callback()
            # Never re-use UUIDs or trust publications from a new compositor.
            self.stop()

    def list_windows(self) -> Sequence[WindowSnapshot]:
        if self._showing_desktop:
            return tuple(replace(window, visible=False) for window in self._windows)
        return self._windows

    def _signal(self, _proxy, sender, name, parameters) -> None:
        if sender == self._owner and name == "showingDesktopChanged":
            self._showing_desktop = parameters.unpack()[0] is True
            for callback in tuple(self._callbacks.values()):
                callback()

    def watch(self, callback: Callable[[], None]) -> object:
        handle = object()
        self._callbacks[handle] = callback
        return handle

    def unwatch(self, handle: object) -> None:
        self._callbacks.pop(handle, None)

    def action(self, window_id: WindowId, action: str) -> ActionResult:
        if self._stopped or not any(row.id == window_id for row in self._windows):
            return ActionResult.NOT_FOUND
        operations = {
            "activate": "window.minimized = false; workspace.activeWindow = window;",
            "minimize": (
                "if (!window.minimizable) return false;\nwindow.minimized = true;"
            ),
            "close": "if (!window.closeable) return false; window.closeWindow();",
        }
        operation = operations.get(action)
        if operation is None:
            return ActionResult.UNSUPPORTED
        token = uuid.uuid4().hex
        plugin = f"{self._plugin}-action-{token}"
        self._actions[token] = None
        identifier = json.dumps(str(window_id.value).removeprefix("kwin:"))
        destination = json.dumps(self._bus.get_unique_name())
        path = json.dumps(self._path)
        encoded_token = json.dumps(token)
        source = f"""const id = {identifier};
const ok = (function() {{
 const window = workspace.stackingOrder.find(
     w => String(w.internalId) === id && !w.deleted);
 if (!window) return false;
 {operation}
 return true;
}})();
callDBus({destination}, {path}, "org.docking.KWinBridge",
         "ActionResult", {encoded_token}, ok);
"""
        try:
            self._load(source, plugin)
            self._wait(
                lambda: self._actions.get(token) is not None or self._stopped, 750
            )
            return (
                ActionResult.OK
                if self._actions.get(token) is True
                else ActionResult.FAILED
            )
        except (GLib.Error, OSError, RuntimeError):
            return ActionResult.FAILED
        finally:
            self._unload(plugin)
            self._actions.pop(token, None)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._owner_watch:
            self._proxy.disconnect(self._owner_watch)
            self._owner_watch = 0
        if self._signal_watch:
            self._proxy.disconnect(self._signal_watch)
            self._signal_watch = 0
        for plugin in tuple(self._loaded):
            self._unload(plugin)
        if self._registration:
            self._bus.unregister_object(self._registration)
            self._registration = 0
        self._callbacks.clear()
        self._windows = ()
        self._directory.cleanup()


def snapshots_from_json(payload: str) -> tuple[WindowSnapshot, ...]:
    rows = json.loads(payload)
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Invalid window list")
    windows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            identifier = str(uuid.UUID(str(row.get("id", "")).strip("{}")))
        except ValueError:
            continue
        geometry = row.get("geometry")
        rect = None
        if (
            isinstance(geometry, list)
            and len(geometry) == 4
            and all(
                not isinstance(value, bool)
                and isinstance(value, int | float)
                and math.isfinite(value)
                for value in geometry
            )
        ):
            x, y, width, height = geometry
            if width > 0 and height > 0:
                left, top = math.floor(x), math.floor(y)
                rect = Rect(
                    left, top, math.ceil(x + width) - left, math.ceil(y + height) - top
                )
        pid = row.get("pid")
        windows.append(
            WindowSnapshot(
                id=WindowId(DisplayServer.WAYLAND, f"kwin:{{{identifier}}}"),
                desktop_id="",
                title=str(row.get("title") or "Window"),
                app_id=str(row.get("app_id") or row.get("wm_class") or ""),
                wm_class=str(row.get("wm_class") or ""),
                pid=pid if isinstance(pid, int) and pid > 0 else None,
                active=row.get("active") is True,
                minimized=row.get("minimized") is True,
                maximized=row.get("maximized") is True,
                fullscreen=row.get("fullscreen") is True,
                geometry=rect,
                workspace_id=row.get("workspace_id"),
                sticky=row.get("sticky") is True,
                dialog=row.get("dialog") is True,
                visible=row.get("visible") is True,
                skip_taskbar=row.get("taskbar") is False,
                on_current_workspace=row.get("on_current_workspace") is True,
                can_activate=row.get("can_activate") is True,
                can_minimize=row.get("can_minimize") is True,
                can_close=row.get("can_close") is True,
                can_preview=True,
            )
        )
    return tuple(windows)


class KWinWindowService(NativeWindowService):
    def __init__(
        self,
        *,
        bridge: KWinBridgeClient,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        config: Config | None = None,
    ) -> None:
        super().__init__(
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
            config=config,
        )
        self._bridge = bridge
        self._watch: object | None = None

    def start(self) -> None:
        if self._watch is not None:
            return
        self._watch = self._bridge.watch(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.replace_windows(self._bridge.list_windows())

    def stop(self) -> None:
        if self._watch is not None:
            self._bridge.unwatch(self._watch)
            self._watch = None
        self._bridge.stop()
        super().stop()

    def perform(self, window_id: WindowId, action: str) -> ActionResult:
        return self._bridge.action(window_id, action)


class KWinDesktopActionService(DesktopActionService):
    def __init__(self, proxy: Gio.DBusProxy) -> None:
        # Persistent caller ownership is required by KWin's Show Desktop mode.
        self._proxy = proxy

    @classmethod
    def connect(cls) -> KWinDesktopActionService | None:
        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                "org.kde.KWin",
                "/KWin",
                "org.kde.KWin",
                None,
            )
            return cls(proxy) if proxy.get_name_owner() else None
        except GLib.Error:
            return None

    def start(self) -> None:
        """Proxy retains the requesting bus connection."""

    def stop(self) -> None:
        """Do not change desktop state on shutdown."""

    def show_desktop(self, show: bool | None = None) -> ActionResult:
        try:
            if show is None:
                state = self._proxy.call_sync(
                    "org.freedesktop.DBus.Properties.Get",
                    GLib.Variant("(ss)", ("org.kde.KWin", "showingDesktop")),
                    Gio.DBusCallFlags.NO_AUTO_START,
                    250,
                    None,
                ).unpack()[0]
                show = not bool(state)
            self._proxy.call_sync(
                "showDesktop",
                GLib.Variant("(b)", (show,)),
                Gio.DBusCallFlags.NO_AUTO_START,
                250,
                None,
            )
            return ActionResult.OK
        except GLib.Error:
            return ActionResult.FAILED
