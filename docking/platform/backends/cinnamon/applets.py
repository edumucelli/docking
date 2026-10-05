"""Native Cinnamon idle and compositor-owned color-picking services."""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING
from uuid import uuid4

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from docking.platform.backends.base import IdleService, ScreenCaptureService
from docking.platform.backends.cinnamon.selection import CinnamonSelection

if TYPE_CHECKING:
    from docking.platform.backends.cinnamon.shell import CinnamonShellClient


class CinnamonIdleService(IdleService):
    def __init__(self, *, proxy: Gio.DBusProxy) -> None:
        self._proxy = proxy

    @classmethod
    def connect(cls) -> CinnamonIdleService | None:
        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                "org.cinnamon.Muffin.IdleMonitor",
                "/org/cinnamon/Muffin/IdleMonitor/Core",
                "org.cinnamon.Muffin.IdleMonitor",
                None,
            )
            service = cls(proxy=proxy)
            return service if service.idle_seconds() is not None else None
        except GLib.Error:
            return None

    def start(self) -> None:
        """D-Bus proxy follows owner changes."""

    def stop(self) -> None:
        """No timers or subscriptions owned by this service."""

    def idle_seconds(self) -> float | None:
        try:
            milliseconds = self._proxy.call_sync(
                "GetIdletime",
                None,
                Gio.DBusCallFlags.NO_AUTO_START,
                250,
                None,
            ).unpack()[0]
        except GLib.Error:
            return None
        return (
            milliseconds / 1000.0
            if type(milliseconds) is int and milliseconds >= 0
            else None
        )


class CinnamonColorPickerService(ScreenCaptureService):
    """Native modal selection followed by a pixel capture without the cursor."""

    interactive = True

    def __init__(self, *, client: CinnamonShellClient) -> None:
        self._client = client
        self._selection = CinnamonSelection(client=client)
        self._busy = False
        self._loop: GLib.MainLoop | None = None
        self._sample_key: str | None = None

    @classmethod
    def connect(
        cls, *, client: CinnamonShellClient
    ) -> CinnamonColorPickerService | None:
        return cls(client=client) if "color" in client.features else None

    def start(self) -> None:
        """No background sampling."""

    def stop(self) -> None:
        self._selection.stop()
        if self._sample_key is not None:
            self._client._eval(
                f"global._dockingColors?.[{self._sample_key}]?.restore(); true"
            )
        if self._loop is not None:
            self._loop.quit()

    def pick_color(self, *, x: int, y: int) -> tuple[int, int, int] | None:
        if self._busy:
            return None
        self._busy = True
        try:
            point = self._selection.select(pick_body="return [x, y];")
            if (
                not isinstance(point, list)
                or len(point) != 2
                or not all(
                    isinstance(v, (float, int)) and math.isfinite(v) for v in point
                )
            ):
                return None
            return self._sample(int(point[0]), int(point[1]))
        finally:
            self._busy = False

    def _sample(self, x: int, y: int) -> tuple[int, int, int] | None:
        key = json.dumps(uuid4().hex)
        self._sample_key = key
        started = self._client._eval(
            """
            (() => {
                const Meta = imports.gi.Meta, GLib = imports.gi.GLib;
                const tracker = Meta.CursorTracker.get_for_display(global.display);
                const visible = tracker.get_pointer_visible();
                const colors = global._dockingColors ||= {};
                const state = {done:false, value:null, restored:false};
                state.restore = () => {
                    if (!state.restored) {
                        tracker.set_pointer_visible(visible); state.restored=true;
                    }
                };
                colors[__KEY__] = state;
                state.timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
                    state.restore(); state.done=true; delete colors[__KEY__];
                    return GLib.SOURCE_REMOVE;
                });
                // Muffin's nested compositor paints a software cursor into the
                // framebuffer. Reading the selected pixel with that cursor
                // present samples its shadow instead of the desktop content.
                tracker.set_pointer_visible(false);
                new imports.gi.Cinnamon.Screenshot().pick_color(__X__, __Y__,
                    (_object, success, color) => {
                        if (!state.done) {
                            state.value = success ?
                                [color.red,color.green,color.blue] : null;
                            state.done=true; state.restore();
                        }
                    });
                return true;
            })()
        """.replace("__KEY__", key)
            .replace("__X__", str(x))
            .replace("__Y__", str(y))
        )
        if started is not True:
            self._cleanup_sample(key)
            return None
        loop = GLib.MainLoop()
        self._loop = loop
        picked: list[tuple[int, int, int]] = []

        def poll() -> bool:
            state = self._client._eval(
                f"(() => {{const s=global._dockingColors?.[{key}];"
                "return s ? {done:s.done,value:s.value} : null;})()"
            )
            if not isinstance(state, dict) or state.get("done"):
                value = state.get("value") if isinstance(state, dict) else None
                if (
                    isinstance(value, list)
                    and len(value) == 3
                    and all(type(v) is int and 0 <= v <= 255 for v in value)
                ):
                    picked.append(tuple(value))
                loop.quit()
            return True

        source = GLib.timeout_add(20, poll)
        try:
            loop.run()
        finally:
            GLib.source_remove(source)
            self._cleanup_sample(key)
            self._loop = None
        return picked[0] if picked else None

    def _cleanup_sample(self, key: str) -> None:
        self._client._eval(
            f"(() => {{const s=global._dockingColors?.[{key}];"
            f"if(s) {{s.done=true; s.restore(); const GLib=imports.gi.GLib;"
            f"GLib.source_remove(s.timer); delete global._dockingColors[{key}];}}"
            "return true;})()"
        )
        self._sample_key = None
