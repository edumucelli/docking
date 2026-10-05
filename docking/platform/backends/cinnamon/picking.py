"""Compositor-owned selection and live native window termination."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    ActionResult,
    WindowId,
    WindowPickService,
    WindowSnapshot,
)
from docking.platform.backends.cinnamon.selection import CinnamonSelection

if TYPE_CHECKING:
    from docking.platform.backends.cinnamon.windows import CinnamonWindowService


PICK_SCRIPT = """
let actor = global.stage.get_actor_at_pos(
    imports.gi.Clutter.PickMode.REACTIVE, __X__, __Y__);
while (actor) {
    const w = actor.get_meta_window?.();
    if (w) {
        if (!w.get_workspace() || w.is_skip_taskbar() ||
            w.get_client_pid?.() === __PID__) return null;
        const type = w.get_window_type(), Meta = imports.gi.Meta;
        if (type === Meta.WindowType.DESKTOP || type === Meta.WindowType.DOCK)
            return null;
        return 'cinnamon:' + global._dockingWindowGeneration + ':' +
            w.get_stable_sequence();
    }
    actor = actor.get_parent();
}
return null;
"""


class CinnamonWindowPickService(WindowPickService):
    interactive = True

    def __init__(self, *, windows: CinnamonWindowService) -> None:
        self._windows = windows
        self._selection = CinnamonSelection(client=windows.shell)

    def start(self) -> None:
        """Create modal input only during selection."""

    def stop(self) -> None:
        self._selection.stop()

    def _snapshot(self, value: object) -> WindowSnapshot | None:
        if not isinstance(value, str):
            return None
        self._windows.refresh()
        if self._windows.shell.last_query_failed:
            return None
        return next(
            (w for w in self._windows.list_all_windows() if w.id.value == value), None
        )

    @staticmethod
    def _pick_script(x: str, y: str) -> str:
        return (
            PICK_SCRIPT.replace("__X__", x)
            .replace("__Y__", y)
            .replace("__PID__", str(os.getpid()))
        )

    def pick_window_at(self, *, x: int, y: int) -> WindowSnapshot | None:
        self._windows.refresh()
        return self._snapshot(
            self._windows.shell._eval(
                "(() => {" + self._pick_script(str(int(x)), str(int(y))) + "})()"
            )
        )

    def select_window(self) -> WindowSnapshot | None:
        self._windows.refresh()
        if self._windows.shell.last_query_failed:
            return None
        return self._snapshot(
            self._selection.select(pick_body=self._pick_script("x", "y"))
        )

    def pid_for(self, window_id: WindowId) -> int | None:
        pid = self._windows.shell.for_window(
            window_id,
            "const pid=w.get_client_pid?.() ?? 0;"
            "return pid > 0 ? pid : w.get_client_type() === "
            "imports.gi.Meta.WindowClientType.X11 ? w.get_pid() : null;",
        )
        return pid if type(pid) is int and pid > 0 and pid != os.getpid() else None

    def kill(self, window_id: WindowId) -> ActionResult:
        # Muffin owns the client connection and resolves its process at the act.
        # A cached PID in Python must never become the termination target.
        result = self._windows.shell.for_window(
            window_id,
            "if (typeof w.kill !== 'function') return 'unsupported';"
            "w.kill(); return 'ok';",
        )
        if result is None:
            return (
                ActionResult.FAILED
                if self._windows.shell.last_request_failed
                else ActionResult.NOT_FOUND
            )
        return self._windows.shell._action_result(result)
