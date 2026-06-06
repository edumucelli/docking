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

"""Wayland foreign-toplevel window service.

PR16 owns the taskbar/window state side of native Wayland support:

  foreign toplevel events
    -> WaylandForeignToplevelWindowService
       -> DockModel.update_running()
       -> WindowSnapshot APIs for menus/actions

Raw protocol handles stay private to this module. The rest of Docking sees only
WindowId, WindowSnapshot, and RunningAppInfo values.
"""

from __future__ import annotations

import importlib
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    WindowId,
    WindowService,
    WindowSnapshot,
)
from docking.platform.launcher import DESKTOP_SUFFIX
from docking.platform.running import RunningAppInfo, RunningWindowInfo

if TYPE_CHECKING:
    from docking.core.items import DockItem
    from docking.platform.backends.wayland.previews import WaylandPreviewHandleTracker
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel

STATE_ACTIVATED = "activated"
STATE_MINIMIZED = "minimized"
STATE_MAXIMIZED = "maximized"
STATE_FULLSCREEN = "fullscreen"
_STATE_BY_VALUE = {
    0: STATE_MAXIMIZED,
    1: STATE_MINIMIZED,
    2: STATE_ACTIVATED,
    3: STATE_FULLSCREEN,
}


@dataclass
class _ToplevelState:
    internal_id: int
    handle: object
    title: str = "Window"
    app_id: str = ""
    desktop_id: str | None = None
    active: bool = False
    minimized: bool | None = None
    maximized: bool | None = None
    fullscreen: bool | None = None
    closed: bool = False
    outputs: set[object] = field(default_factory=set)
    parent: object | None = None

    @property
    def window_id(self) -> WindowId:
        return WindowId(backend=DisplayServer.WAYLAND, value=self.internal_id)


class WaylandAppIdMatcher:
    """Resolve Wayland compositor app IDs to Docking desktop IDs."""

    def __init__(self, launcher: Launcher) -> None:
        self._launcher = launcher
        self._visible_aliases: dict[str, str] = {}

    def sync_visible_items(self, items: Iterable[DockItem]) -> None:
        """Refresh aliases from current pinned and transient dock items."""
        self._visible_aliases.clear()
        for item in items:
            aliases = {
                item.desktop_id,
                item.desktop_id.removesuffix(DESKTOP_SUFFIX),
                getattr(item, "wm_class", "") or "",
            }
            for alias in aliases:
                normalized = _normalize_app_id(alias)
                if normalized:
                    self._visible_aliases[normalized] = item.desktop_id

    def match(self, app_id: str) -> str | None:
        """Return a Docking desktop ID for a compositor app_id."""
        for candidate in _app_id_candidates(app_id):
            visible = self._visible_aliases.get(_normalize_app_id(candidate))
            if visible:
                return visible
            desktop_id = _ensure_desktop_suffix(candidate)
            resolved = self._launcher.resolve(desktop_id, log_failures=False)
            if resolved is not None:
                return resolved.desktop_id
            by_wm_class = self._launcher.resolve_by_wm_class(candidate)
            if by_wm_class is not None:
                return by_wm_class.desktop_id
        return None


class WaylandForeignToplevelWindowService(WindowService):
    """WindowService backed by Wayland foreign-toplevel protocol events."""

    def __init__(
        self,
        *,
        model: DockModel,
        launcher: Launcher,
        protocol: object,
        preview_handles: WaylandPreviewHandleTracker | None = None,
        can_preview: bool = False,
    ) -> None:
        self._model = model
        self._matcher = WaylandAppIdMatcher(launcher=launcher)
        self._protocol = protocol
        self._preview_handles = preview_handles
        self._can_preview = can_preview
        self._next_id = 1
        self._state_by_handle: dict[object, _ToplevelState] = {}
        self._state_by_id: dict[int, _ToplevelState] = {}
        self._last_running: dict[str, RunningAppInfo] = {}

    def start(self) -> None:
        """Start receiving foreign-toplevel events from the adapter."""
        start = getattr(self._protocol, "start", None)
        if callable(start):
            start(self)
        self._publish_running()

    def stop(self) -> None:
        """Stop protocol event delivery and clear published running state."""
        stop = getattr(self._protocol, "stop", None)
        if callable(stop):
            stop()
        self._state_by_handle.clear()
        self._state_by_id.clear()
        self._last_running = {}
        self._model.update_running(running={})

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        """Return current Wayland toplevels matched to one desktop ID."""
        return tuple(
            self._snapshot_for(state) for state in self._states_for_desktop(desktop_id)
        )

    def list_preview_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        """Return menu/preview window rows; capture remains unsupported."""
        return self.list_windows(desktop_id=desktop_id)

    def icon_name_for_desktop(self, desktop_id: str) -> str:
        """Return a generic icon fallback; model icon lookup handles launchers."""
        return "application-x-executable"

    def protocol_handle_for_window_id(self, window_id: WindowId) -> object | None:
        """Return the private protocol handle for same-backend preview services."""
        state = self._state_for_window_id(window_id)
        return state.handle if state is not None else None

    def protocol_handle_for_match(
        self,
        *,
        desktop_id: str | None,
        app_id: str,
        title: str,
    ) -> object | None:
        """Return a protocol handle matching backend-neutral window facts."""
        for state in self._state_by_id.values():
            if state.closed:
                continue
            if state.desktop_id is None:
                self._refresh_match(state=state)
            if desktop_id and state.desktop_id != desktop_id:
                continue
            if title and state.title and state.title == title:
                return state.handle
            if app_id and state.app_id and state.app_id == app_id:
                return state.handle
        return None

    def activate(self, window_id: WindowId) -> ActionResult:
        """Activate one foreign toplevel by backend ID."""
        state = self._state_for_window_id(window_id)
        if state is None:
            return ActionResult.NOT_FOUND
        return self._call_action(state=state, action="activate")

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        """Activate the most recent known toplevel for an app."""
        state = self._first_state_for_desktop(desktop_id)
        if state is None:
            return ActionResult.NOT_FOUND
        return self._call_action(state=state, action="activate")

    def cycle(self, desktop_id: str) -> ActionResult:
        """Cycle policy is reduced to activating a known window for PR16."""
        return self.activate_most_recent(desktop_id=desktop_id)

    def minimize_all(self, desktop_id: str) -> ActionResult:
        """Request minimization for all known windows of an app."""
        states = self._states_for_desktop(desktop_id)
        if not states:
            return ActionResult.NOT_FOUND
        result = ActionResult.OK
        for state in states:
            action_result = self._call_action(state=state, action="set_minimized")
            if action_result is not ActionResult.OK:
                result = action_result
        return result

    def close(self, window_id: WindowId) -> ActionResult:
        """Close one foreign toplevel."""
        state = self._state_for_window_id(window_id)
        if state is None:
            return ActionResult.NOT_FOUND
        return self._call_action(state=state, action="close")

    def close_all(self, desktop_id: str) -> ActionResult:
        """Close all known windows for an app."""
        states = self._states_for_desktop(desktop_id)
        if not states:
            return ActionResult.NOT_FOUND
        result = ActionResult.OK
        for state in states:
            action_result = self._call_action(state=state, action="close")
            if action_result is not ActionResult.OK:
                result = action_result
        return result

    def close_focused(self, desktop_id: str) -> ActionResult:
        """Close the active window for an app, or the first known window."""
        states = self._states_for_desktop(desktop_id)
        if not states:
            return ActionResult.NOT_FOUND
        state = next((item for item in states if item.active), states[0])
        return self._call_action(state=state, action="close")

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        """Activate inactive apps; minimize the active app when possible."""
        states = self._states_for_desktop(desktop_id)
        if not states:
            return ActionResult.NOT_FOUND
        active = next((item for item in states if item.active), None)
        if active is not None:
            minimized = self._call_action(state=active, action="set_minimized")
            if minimized is not ActionResult.UNSUPPORTED:
                return minimized
        return self._call_action(state=states[0], action="activate")

    def toplevel_created(self, handle: object) -> WindowId:
        """Register a newly announced protocol handle."""
        existing = self._state_by_handle.get(handle)
        if existing is not None:
            return existing.window_id
        state = _ToplevelState(internal_id=self._next_id, handle=handle)
        self._next_id += 1
        self._state_by_handle[handle] = state
        self._state_by_id[state.internal_id] = state
        return state.window_id

    def title_changed(self, handle: object, title: str) -> None:
        """Apply a foreign-toplevel title event."""
        state = self._ensure_state(handle=handle)
        state.title = title or "Window"

    def app_id_changed(self, handle: object, app_id: str) -> None:
        """Apply a foreign-toplevel app_id event."""
        state = self._ensure_state(handle=handle)
        state.app_id = app_id.strip()
        self._refresh_match(state=state)

    def state_changed(self, handle: object, states: Iterable[object]) -> None:
        """Apply a foreign-toplevel state-list event."""
        state = self._ensure_state(handle=handle)
        normalized = _normalize_states(states)
        state.active = STATE_ACTIVATED in normalized
        state.minimized = STATE_MINIMIZED in normalized
        state.maximized = STATE_MAXIMIZED in normalized
        state.fullscreen = STATE_FULLSCREEN in normalized

    def output_entered(self, handle: object, output: object) -> None:
        """Track the outputs a toplevel is visible on."""
        self._ensure_state(handle=handle).outputs.add(output)

    def output_left(self, handle: object, output: object) -> None:
        """Track the outputs a toplevel is no longer visible on."""
        self._ensure_state(handle=handle).outputs.discard(output)

    def parent_changed(self, handle: object, parent: object | None) -> None:
        """Track transient parent relationships, if provided."""
        self._ensure_state(handle=handle).parent = parent

    def done(self, handle: object) -> None:
        """Publish state after a protocol atomic update batch."""
        state = self._ensure_state(handle=handle)
        self._refresh_match(state=state)
        self._publish_running()

    def closed(self, handle: object) -> None:
        """Remove a closed foreign toplevel and publish the new aggregate."""
        state = self._state_by_handle.pop(handle, None)
        if state is None:
            return
        state.closed = True
        self._state_by_id.pop(state.internal_id, None)
        self._publish_running()

    def _ensure_state(self, *, handle: object) -> _ToplevelState:
        state = self._state_by_handle.get(handle)
        if state is None:
            self.toplevel_created(handle=handle)
            state = self._state_by_handle[handle]
        return state

    def _refresh_match(self, *, state: _ToplevelState) -> None:
        self._matcher.sync_visible_items(self._model.visible_items())
        state.desktop_id = self._matcher.match(state.app_id) if state.app_id else None

    def _publish_running(self) -> None:
        self._matcher.sync_visible_items(self._model.visible_items())
        windows_by_desktop: dict[str, list[RunningWindowInfo]] = {}
        for state in self._state_by_id.values():
            if state.closed:
                continue
            if state.desktop_id is None:
                self._refresh_match(state=state)
            if state.desktop_id is None:
                continue
            windows_by_desktop.setdefault(state.desktop_id, []).append(
                RunningWindowInfo(
                    desktop_id=state.desktop_id,
                    xid=state.internal_id,
                    window_id=state.window_id,
                    active=state.active,
                    urgent=False,
                    window=state.handle,
                )
            )
        running = {
            desktop_id: RunningAppInfo.from_windows(items)
            for desktop_id, items in windows_by_desktop.items()
        }
        self._last_running = dict(running)
        self._model.update_running(running=running)

    def _states_for_desktop(self, desktop_id: str) -> list[_ToplevelState]:
        return [
            state
            for state in self._state_by_id.values()
            if not state.closed and state.desktop_id == desktop_id
        ]

    def _first_state_for_desktop(self, desktop_id: str) -> _ToplevelState | None:
        states = self._states_for_desktop(desktop_id)
        if not states:
            return None
        return next((state for state in states if state.active), states[0])

    def _state_for_window_id(self, window_id: WindowId) -> _ToplevelState | None:
        if window_id.backend is not DisplayServer.WAYLAND:
            return None
        try:
            internal_id = int(window_id.value)
        except (TypeError, ValueError):
            return None
        state = self._state_by_id.get(internal_id)
        if state is None or state.closed:
            return None
        return state

    def _snapshot_for(self, state: _ToplevelState) -> WindowSnapshot:
        preview_handles = self._preview_handles
        if preview_handles is not None:
            preview_handles.associate_window(
                window_id=state.window_id,
                desktop_id=state.desktop_id,
                app_id=state.app_id,
                title=state.title,
            )
        return WindowSnapshot(
            id=state.window_id,
            desktop_id=state.desktop_id or "",
            title=state.title or "Window",
            app_id=state.app_id or None,
            active=state.active,
            minimized=state.minimized,
            maximized=state.maximized,
            fullscreen=state.fullscreen,
            can_activate=self._supports_action("activate", state.handle),
            can_minimize=self._supports_action("set_minimized", state.handle),
            can_close=self._supports_action("close", state.handle),
            can_preview=preview_handles.can_preview(state.window_id)
            if preview_handles is not None
            else self._can_preview,
        )

    def _supports_action(self, action: str, handle: object) -> bool:
        protocol_supports_action = getattr(self._protocol, "supports_action", None)
        if callable(protocol_supports_action):
            return bool(protocol_supports_action(action, handle))
        return callable(getattr(self._protocol, action, None)) or callable(
            getattr(handle, action, None)
        )

    def _call_action(self, *, state: _ToplevelState, action: str) -> ActionResult:
        if not self._supports_action(action, state.handle):
            return ActionResult.UNSUPPORTED
        protocol_method = getattr(self._protocol, action, None)
        if callable(protocol_method):
            protocol_method(state.handle)
            return ActionResult.OK
        handle_method = getattr(state.handle, action, None)
        if callable(handle_method):
            handle_method()
            return ActionResult.OK
        return ActionResult.UNSUPPORTED


def load_foreign_toplevel_protocol() -> object | None:
    """Load an optional foreign-toplevel protocol adapter.

    The Python Wayland binding is intentionally optional. Systems without a
    binding or compositor support keep using ReducedWindowService.
    """
    try:
        importlib.import_module(
            "docking.platform.backends.wayland.protocols."
            "wlr_foreign_toplevel_management_unstable_v1"
        )
    except ImportError:
        return None
    # A concrete adapter can be added here once the project chooses and packages
    # a live pywayland registry/event-loop bridge. The generated protocol module
    # is vendored, but the service above stays independent from the binding so it
    # can be tested without a live compositor.
    return None


def _normalize_app_id(value: str) -> str:
    return value.strip().removesuffix(DESKTOP_SUFFIX).lower()


def _ensure_desktop_suffix(value: str) -> str:
    stripped = value.strip()
    if stripped.endswith(DESKTOP_SUFFIX):
        return stripped
    return f"{stripped}{DESKTOP_SUFFIX}"


def _app_id_candidates(app_id: str) -> list[str]:
    stripped = app_id.strip()
    if not stripped:
        return []
    candidates = [
        stripped,
        stripped.removesuffix(DESKTOP_SUFFIX),
        stripped.lower(),
        stripped.lower().removesuffix(DESKTOP_SUFFIX),
    ]
    if "." in stripped:
        candidates.append(stripped.split(".")[-1])
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _normalize_state(value: object) -> str:
    if isinstance(value, int):
        return _STATE_BY_VALUE.get(value, str(value)).strip().lower()
    if isinstance(value, str):
        return value.strip().lower()
    name = getattr(value, "name", "")
    if isinstance(name, str) and name:
        return name.strip().lower()
    return str(value).strip().lower()


def _normalize_states(values: Iterable[object]) -> set[str]:
    if isinstance(values, bytes | bytearray | memoryview):
        data = bytes(values)
        size = struct.calcsize("I")
        return {
            _normalize_state(value)
            for (value,) in struct.iter_unpack(
                "I", data[: len(data) - len(data) % size]
            )
        }
    return {_normalize_state(value) for value in values}
