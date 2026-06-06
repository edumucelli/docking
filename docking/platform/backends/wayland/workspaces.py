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

"""Wayland workspace service backed by ext-workspace-v1-style events."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from docking.platform.backends.base import (
    ActionResult,
    WorkspaceService,
    WorkspaceSnapshot,
)

STATE_ACTIVE = "active"
CAPABILITY_ACTIVATE = "activate"
STATE_ACTIVE_BIT = 1
CAPABILITY_ACTIVATE_BIT = 1


@dataclass
class _WorkspaceState:
    internal_id: int
    handle: object
    protocol_id: str = ""
    name: str = ""
    active: bool = False
    can_activate: bool = False
    removed: bool = False

    @property
    def snapshot_id(self) -> str:
        return self.protocol_id or str(self.internal_id)

    @property
    def display_name(self) -> str:
        return self.name or self.snapshot_id


class WaylandWorkspaceService(WorkspaceService):
    """WorkspaceService for standard Wayland workspace protocol events."""

    def __init__(self, *, protocol: object) -> None:
        self._protocol = protocol
        self._next_id = 0
        self._state_by_handle: dict[object, _WorkspaceState] = {}
        self._state_by_id: dict[str, _WorkspaceState] = {}
        self._watchers: dict[int, Callable[[], None]] = {}
        self._next_watch_id = 1
        self._active_id: str | None = None

    def start(self) -> None:
        start = getattr(self._protocol, "start", None)
        if callable(start):
            start(self)

    def stop(self) -> None:
        stop = getattr(self._protocol, "stop", None)
        if callable(stop):
            stop()
        self._state_by_handle.clear()
        self._state_by_id.clear()
        self._watchers.clear()
        self._active_id = None

    def list_workspaces(self) -> Sequence[WorkspaceSnapshot]:
        return tuple(
            WorkspaceSnapshot(
                id=state.snapshot_id,
                number=state.internal_id,
                name=state.display_name,
                active=state.active,
            )
            for state in sorted(
                self._state_by_id.values(), key=lambda item: item.internal_id
            )
            if not state.removed
        )

    def active_workspace(self) -> WorkspaceSnapshot | None:
        for workspace in self.list_workspaces():
            if workspace.active:
                return workspace
        return None

    def activate(self, workspace_id: str) -> ActionResult:
        state = self._resolve_workspace(workspace_id)
        if state is None:
            return ActionResult.NOT_FOUND
        if not state.can_activate:
            return ActionResult.UNSUPPORTED
        protocol_method = getattr(self._protocol, "activate", None)
        if callable(protocol_method):
            protocol_method(state.handle)
            return ActionResult.OK
        handle_method = getattr(state.handle, "activate", None)
        if callable(handle_method):
            handle_method()
            return ActionResult.OK
        return ActionResult.UNSUPPORTED

    def watch_active_workspace(self, on_change: Callable[[], None]) -> object | None:
        watch_id = self._next_watch_id
        self._next_watch_id += 1
        self._watchers[watch_id] = on_change
        return watch_id

    def unwatch_active_workspace(self, handle: object) -> None:
        if isinstance(handle, int):
            self._watchers.pop(handle, None)

    def workspace_created(self, handle: object) -> str:
        state = self._state_by_handle.get(handle)
        if state is not None:
            return state.snapshot_id
        state = _WorkspaceState(internal_id=self._next_id, handle=handle)
        self._next_id += 1
        self._state_by_handle[handle] = state
        self._state_by_id[state.snapshot_id] = state
        return state.snapshot_id

    def id_changed(self, handle: object, workspace_id: str) -> None:
        state = self._ensure_state(handle=handle)
        old_id = state.snapshot_id
        state.protocol_id = workspace_id.strip()
        self._state_by_id.pop(old_id, None)
        self._state_by_id[state.snapshot_id] = state

    def name_changed(self, handle: object, name: str) -> None:
        self._ensure_state(handle=handle).name = name.strip()

    def state_changed(self, handle: object, states: Iterable[object] | int) -> None:
        state = self._ensure_state(handle=handle)
        state.active = _has_flag(
            states,
            token=STATE_ACTIVE,
            bit=STATE_ACTIVE_BIT,
        )

    def capabilities_changed(
        self, handle: object, capabilities: Iterable[object] | int
    ) -> None:
        state = self._ensure_state(handle=handle)
        state.can_activate = _has_flag(
            capabilities,
            token=CAPABILITY_ACTIVATE,
            bit=CAPABILITY_ACTIVATE_BIT,
        )

    def done(self) -> None:
        active = self.active_workspace()
        active_id = active.id if active is not None else None
        if active_id == self._active_id:
            return
        self._active_id = active_id
        for callback in tuple(self._watchers.values()):
            callback()

    def removed(self, handle: object) -> None:
        state = self._state_by_handle.pop(handle, None)
        if state is None:
            return
        state.removed = True
        self._state_by_id.pop(state.snapshot_id, None)
        self.done()

    def _resolve_workspace(self, workspace_id: str) -> _WorkspaceState | None:
        state = self._state_by_id.get(workspace_id)
        if state is not None and not state.removed:
            return state
        for candidate in self._state_by_id.values():
            if candidate.removed:
                continue
            if (
                candidate.name == workspace_id
                or str(candidate.internal_id) == workspace_id
            ):
                return candidate
        return None

    def _ensure_state(self, *, handle: object) -> _WorkspaceState:
        state = self._state_by_handle.get(handle)
        if state is None:
            self.workspace_created(handle=handle)
            state = self._state_by_handle[handle]
        return state


def load_workspace_protocol() -> object | None:
    """Load an optional live ext-workspace adapter, if one exists."""
    try:
        importlib.import_module(
            "docking.platform.backends.wayland.protocols.ext_workspace_v1"
        )
    except ImportError:
        return None
    # Generated bindings are available, but the live registry/event-loop adapter
    # is separate future work. Keep the service fail-closed until then.
    return None


def _normalize_token(value: object) -> str:
    if isinstance(value, str):
        return value.strip().lower()
    name = getattr(value, "name", "")
    if isinstance(name, str) and name:
        return name.strip().lower()
    return str(value).strip().lower()


def _has_flag(values: Iterable[object] | int, *, token: str, bit: int) -> bool:
    if isinstance(values, int):
        return bool(values & bit)
    return token in {_normalize_token(value) for value in values}
