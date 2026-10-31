"""Windows WorkspaceService: worker-owned shell calls and GTK snapshots."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Sequence
from types import MappingProxyType
from typing import Protocol

from gi.repository import GLib

from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    WorkspaceService,
    WorkspaceSnapshot,
)

from .virtual_desktops import (
    VirtualDesktopState,
    WindowsVirtualDesktopAPI,
    WindowWorkspace,
)

log = get_logger("windows.workspaces")
_EMPTY = VirtualDesktopState((), MappingProxyType({}))


class WorkspaceAPI(Protocol):
    def supported(self) -> bool: ...
    def read(self) -> VirtualDesktopState: ...
    def activate(
        self, desktop_id: str, cancelled: Callable[[], bool]
    ) -> ActionResult: ...
    def set_pinned(self, hwnd: int, pinned: bool) -> None: ...


class WindowsWorkspaceService(WorkspaceService):
    """OK accepts a user request; the worker verifies its native transition."""

    def __init__(self, *, api: WorkspaceAPI | None = None) -> None:
        self._api = api if api is not None else WindowsVirtualDesktopAPI()
        self.supported = self._api.supported()
        self._condition = threading.Condition()
        self._state = _EMPTY
        self._available = False
        self._stopped = True
        self._generation = 0
        self._thread: threading.Thread | None = None
        self._requests: deque[tuple[int, str, Callable[[], ActionResult] | None]] = (
            deque()
        )
        self._sources: set[int] = set()
        self._watchers: dict[int, Callable[[], None]] = {}
        self._next_watch = 0
        self._busy = False
        self._last_result: ActionResult | None = None
        self._scope: tuple[int, bool] | None = None

    @property
    def available(self) -> bool:
        with self._condition:
            return self._available

    @property
    def pending(self) -> bool:
        with self._condition:
            return self._busy or bool(self._requests)

    @property
    def last_result(self) -> ActionResult | None:
        with self._condition:
            return self._last_result

    def start(self) -> None:
        with self._condition:
            if not self._stopped:
                return
            self._generation += 1
            self._stopped = False
            self._last_result = None
            if self.supported and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(
                    target=self._run, name="windows-workspaces", daemon=True
                )
                self._thread.start()
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._generation += 1
            self._stopped = True
            self._requests.clear()
            self._scope = None
            self._state, self._available = _EMPTY, False
            for source in self._sources:
                GLib.source_remove(source)
            self._sources.clear()
            self._watchers.clear()
            thread = self._thread
            self._condition.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(0.5)

    def list_workspaces(self) -> Sequence[WorkspaceSnapshot]:
        with self._condition:
            return self._state.desktops

    def set_surface_scope(self, hwnd: int, *, current_workspace_only: bool) -> None:
        with self._condition:
            self._scope = hwnd, not current_workspace_only
            self._condition.notify_all()

    def active_workspace(self) -> WorkspaceSnapshot | None:
        return next((entry for entry in self.list_workspaces() if entry.active), None)

    def membership(self, hwnd: int, pid: int) -> WindowWorkspace | None:
        with self._condition:
            value = self._state.windows.get(hwnd)
            return value if value is not None and value.pid == pid else None

    def activate(self, workspace_id: str) -> ActionResult:
        return self.activate_then(workspace_id, None)

    def activate_then(
        self, workspace_id: str, after: Callable[[], ActionResult] | None
    ) -> ActionResult:
        try:
            identifier = str(uuid.UUID(workspace_id))
        except ValueError:
            return ActionResult.NOT_FOUND
        with self._condition:
            if self._stopped:
                return ActionResult.NOT_FOUND
            if not self._available:
                return ActionResult.UNSUPPORTED
            if identifier not in {entry.id for entry in self._state.desktops}:
                return ActionResult.NOT_FOUND
            if len(self._requests) >= 64:
                return ActionResult.FAILED
            self._requests.append((self._generation, identifier, after))
            self._last_result = None
            self._condition.notify_all()
            return ActionResult.OK

    def watch_active_workspace(self, on_change: Callable[[], None]) -> object:
        with self._condition:
            self._next_watch += 1
            self._watchers[self._next_watch] = on_change
            return self._next_watch

    def unwatch_active_workspace(self, handle: object) -> None:
        if isinstance(handle, int):
            with self._condition:
                self._watchers.pop(handle, None)

    def _cancelled(self, generation: int) -> bool:
        with self._condition:
            return self._stopped or generation != self._generation

    def _post(self, generation: int, state: VirtualDesktopState) -> None:
        with self._condition:
            if self._cancelled(generation):
                return
            source = 0

            def dispatch():
                with self._condition:
                    self._sources.discard(source)
                    if self._cancelled(generation):
                        return False
                    changed = self._state != state or self._available != bool(
                        state.desktops
                    )
                    self._state, self._available = state, bool(state.desktops)
                    callbacks = tuple(self._watchers.values()) if changed else ()
                for callback in callbacks:
                    callback()
                return False

            source = GLib.idle_add(dispatch)
            self._sources.add(source)

    def _transition(
        self, generation: int, identifier: str, after: Callable[[], ActionResult] | None
    ) -> tuple[VirtualDesktopState, ActionResult]:
        result = self._api.activate(identifier, lambda: self._cancelled(generation))
        if result is not ActionResult.OK:
            return self._api.read(), result
        deadline = time.monotonic() + 2.0
        while not self._cancelled(generation):
            state = self._api.read()
            if any(entry.id == identifier and entry.active for entry in state.desktops):
                if self._cancelled(generation):
                    break
                return state, after() if after is not None else ActionResult.OK
            if time.monotonic() >= deadline:
                return state, ActionResult.FAILED
            with self._condition:
                self._condition.wait(0.05)
        return _EMPTY, ActionResult.NOT_FOUND

    def _run(self) -> None:
        current = threading.current_thread()
        try:
            while True:
                with self._condition:
                    if self._stopped:
                        return
                    generation = self._generation
                    scope = self._scope
                    request = self._requests.popleft() if self._requests else None
                    self._busy = request is not None
                result = None
                if scope is not None and not self._cancelled(generation):
                    try:
                        self._api.set_pinned(*scope)
                    except OSError as exc:
                        # A newly mapped HWND may not have a shell view yet.
                        # Retry on the next poll without losing desktop data.
                        log.debug("Could not apply dock workspace scope: %s", exc)
                try:
                    if request is None:
                        state = self._api.read()
                    elif request[0] == generation:
                        state, result = self._transition(*request)
                    else:
                        continue
                except OSError as exc:
                    log.debug("Virtual desktop service unavailable: %s", exc)
                    state, result = _EMPTY, ActionResult.FAILED if request else None
                self._post(generation, state)
                with self._condition:
                    if result is not None and not self._cancelled(generation):
                        self._last_result = result
                    self._busy = False
                    if not self._stopped and not self._requests:
                        self._condition.wait(0.5)
        finally:
            with self._condition:
                if self._thread is current:
                    self._thread, self._busy = None, False
