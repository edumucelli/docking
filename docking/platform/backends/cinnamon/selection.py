"""Owned Cinnamon modal selections shared by color and window picking."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

from gi.repository import GLib

if TYPE_CHECKING:
    from docking.platform.backends.cinnamon.shell import CinnamonShellClient


class CinnamonSelection:
    def __init__(self, *, client: CinnamonShellClient) -> None:
        self._client = client
        self._token: str | None = None
        self._loop: GLib.MainLoop | None = None

    def stop(self) -> None:
        if self._token is not None:
            self._client._eval(
                f"global._dockingPicks?.[{json.dumps(self._token)}]?.finish(null); true"
            )
        if self._loop is not None:
            self._loop.quit()

    def select(self, *, pick_body: str) -> object:
        if self._token is not None:
            return None
        bus_name = self._client.bus_name
        if bus_name is None:
            return None
        token = uuid4().hex
        self._token = token
        key = json.dumps(token)
        started = self._client._eval(
            """
            (() => {
                const Main = imports.ui.main, Clutter = imports.gi.Clutter;
                const GLib = imports.gi.GLib, Meta = imports.gi.Meta;
                const Gio = imports.gi.Gio;
                const picks = global._dockingPicks ||= {};
                const actor = new imports.gi.St.Widget({reactive:true,
                    width:global.stage.width, height:global.stage.height});
                Main.uiGroup.add_child(actor);
                if (!Main.pushModal(actor)) {actor.destroy(); return false;}
                const state = {done:false, value:null, signal:0, timer:0, watch:0};
                picks[__KEY__] = state;
                state.finish = value => {
                    if (state.done) return;
                    state.done = true; state.value = value;
                    global.stage.disconnect(state.signal);
                    if (state.timer) GLib.source_remove(state.timer);
                    Main.popModal(actor); actor.hide();
                    GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                        actor.destroy(); return GLib.SOURCE_REMOVE;
                    });
                    global.display.set_cursor(Meta.Cursor.DEFAULT);
                    state.timer = GLib.timeout_add_seconds(
                        GLib.PRIORITY_DEFAULT, 2, () => {
                            state.timer=0; state.dispose(); return GLib.SOURCE_REMOVE;
                        });
                };
                state.dispose = () => {
                    state.finish(null);
                    if (state.timer) {GLib.source_remove(state.timer); state.timer=0;}
                    if (state.watch) {Gio.bus_unwatch_name(state.watch); state.watch=0;}
                    delete picks[__KEY__];
                };
                state.signal = global.stage.connect('captured-event',
                    (_stage, event) => {
                    if (event.type() === Clutter.EventType.KEY_PRESS &&
                        event.get_key_symbol() === Clutter.KEY_Escape) {
                        state.finish(null); return Clutter.EVENT_STOP;
                    }
                    if (event.type() !== Clutter.EventType.BUTTON_PRESS)
                        return Clutter.EVENT_PROPAGATE;
                    if (event.get_button() !== 1) {
                        state.finish(null); return Clutter.EVENT_STOP;
                    }
                    const [x, y] = event.get_coords();
                    actor.hide();
                    try { state.finish((() => { __PICK__ })()); }
                    catch (_) { state.finish(null); }
                    return Clutter.EVENT_STOP;
                });
                state.timer = GLib.timeout_add_seconds(
                    GLib.PRIORITY_DEFAULT, 60, () => {
                    state.timer = 0; state.dispose();
                    return GLib.SOURCE_REMOVE;
                });
                state.watch = Gio.bus_watch_name(Gio.BusType.SESSION, __BUS__,
                    Gio.BusNameWatcherFlags.NONE, () => {}, () => state.dispose());
                global.display.set_cursor(Meta.Cursor.CROSSHAIR);
                return true;
            })()
        """.replace("__KEY__", key)
            .replace("__PICK__", pick_body)
            .replace("__BUS__", json.dumps(bus_name))
        )
        if started is not True:
            self._token = None
            return None
        loop = GLib.MainLoop()
        self._loop = loop
        result: list[object] = []

        def poll() -> bool:
            state = self._client._eval(
                f"(() => {{const s = global._dockingPicks?.[{key}];"
                "return s ? {done:s.done, value:s.value} : null;})()"
            )
            if not isinstance(state, dict) or state.get("done"):
                result.append(state.get("value") if isinstance(state, dict) else None)
                loop.quit()
            return True

        source = GLib.timeout_add(50, poll)
        try:
            loop.run()
        finally:
            GLib.source_remove(source)
            self._client._eval(
                f"(() => {{const s = global._dockingPicks?.[{key}];"
                "if (s) s.dispose();"
                "return true;})()"
            )
            self._token = self._loop = None
        return result[0] if result else None
