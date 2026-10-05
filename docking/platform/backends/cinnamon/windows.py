"""Cinnamon shell snapshots and workspace-aware taskbar window actions."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from docking.platform.backends.base import ActionResult, DisplayServer, WindowId
from docking.platform.backends.cinnamon.muffin import MuffinWindowService
from docking.platform.backends.diagnostics import WindowReason

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.base import WindowSnapshot
    from docking.platform.backends.cinnamon.muffin import _MuffinWindow
    from docking.platform.backends.cinnamon.shell import CinnamonShellClient
    from docking.platform.model import DockModel


class CinnamonWindowService(MuffinWindowService):
    """Reuse snapshot matching while providing Cinnamon's native window actions."""

    poll_interval_ms = 250
    query_name = "Cinnamon shell"

    def __init__(
        self,
        *,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        client: CinnamonShellClient,
        config: Config | None = None,
    ) -> None:
        super().__init__(
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
            client=client,
        )
        self._shell = client
        self._config = config
        self._listeners: dict[object, Callable[[], None]] = {}
        self._sticky: set[int] = set()

    @property
    def shell(self) -> CinnamonShellClient:
        return self._shell

    def watch(self, callback: Callable[[], None]) -> object:
        handle = object()
        self._listeners[handle] = callback
        return handle

    def unwatch(self, handle: object) -> None:
        self._listeners.pop(handle, None)

    def refresh(self) -> None:
        super().refresh()
        if not self._shell.last_query_failed:
            for callback in tuple(self._listeners.values()):
                callback()

    def stop(self) -> None:
        super().stop()
        self._listeners.clear()
        self._sticky.clear()

    def _on_current_workspace(self, window: _MuffinWindow) -> bool:
        if self._config is None or not self._config.current_workspace_only:
            return True
        active = next((w.id for w in self._shell.workspace_snapshots if w.active), None)
        return (
            active is None
            or window.workspace_id == active
            or window.muffin_id in self._sticky
        )

    def _running_windows(self) -> Sequence[_MuffinWindow]:
        self._sticky = {
            row["id"]
            for row in getattr(self._shell, "rows", ())
            if row.get("sticky") is True and type(row.get("id")) is int
        }
        return tuple(w for w in self._windows.values() if self._on_current_workspace(w))

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return tuple(
            self._snapshot(w)
            for w in self._windows.values()
            if w.desktop_id == desktop_id and self._on_current_workspace(w)
        )

    def _window_id(self, row: Mapping[str, Any], sequence: int) -> WindowId | None:
        generation = row.get("generation")
        if (
            type(row.get("id")) is not int
            or sequence <= 0
            or not isinstance(generation, str)
            or not generation
            or ":" in generation
        ):
            return None
        return WindowId(
            backend=DisplayServer.WAYLAND, value=f"cinnamon:{generation}:{sequence}"
        )

    def _excluded_reason(self, row: Mapping[str, Any]) -> WindowReason | None:
        if row.get("pid") == os.getpid():
            return WindowReason.OWN_PROCESS
        if row.get("desktop-or-dock"):
            return WindowReason.DESKTOP_OR_DOCK
        return super()._excluded_reason(row)

    def _snapshot(self, window: _MuffinWindow) -> WindowSnapshot:
        return replace(
            MuffinWindowService._snapshot(window),
            can_preview="previews" in getattr(self._shell, "features", ()),
            can_activate=True,
            can_minimize=window.can_minimize,
            can_close=window.can_close,
        )

    def activate(self, window_id: WindowId) -> ActionResult:
        self.refresh()
        return self._act((window_id,), "activate")

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        return self.toggle_focus(desktop_id)

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        windows = self._for_desktop(desktop_id)
        if any(window.active for window in windows):
            return self._act(tuple(w.window_id for w in windows), "minimize")
        return self._act(tuple(w.window_id for w in windows[:1]), "activate")

    def cycle(self, desktop_id: str) -> ActionResult:
        windows = self._for_desktop(desktop_id)
        if not windows:
            return self._act((), "activate")
        # Stable creation order prevents MRU updates from reversing each cycle.
        ordered = sorted(windows, key=lambda window: window.muffin_id)
        active = next((i for i, w in enumerate(ordered) if w.active), None)
        target = (
            ordered[(active + 1) % len(ordered)] if active is not None else windows[0]
        )
        return self._act((target.window_id,), "activate")

    def minimize_all(self, desktop_id: str) -> ActionResult:
        return self._act(
            tuple(w.window_id for w in self._for_desktop(desktop_id)), "minimize"
        )

    def close(self, window_id: WindowId) -> ActionResult:
        self.refresh()
        return self._act((window_id,), "close")

    def close_all(self, desktop_id: str) -> ActionResult:
        return self._act(
            tuple(w.window_id for w in self._for_desktop(desktop_id)), "close"
        )

    def close_focused(self, desktop_id: str) -> ActionResult:
        return self._act(
            tuple(w.window_id for w in self._for_desktop(desktop_id) if w.active),
            "close",
        )

    def _for_desktop(self, desktop_id: str) -> list[_MuffinWindow]:
        self.refresh()
        return [
            w
            for w in self._windows.values()
            if w.desktop_id == desktop_id and self._on_current_workspace(w)
        ]

    def _act(self, window_ids: Sequence[WindowId], action: str) -> ActionResult:
        if self._shell.last_query_failed:
            return ActionResult.FAILED
        known = {w.window_id for w in self._windows.values()}
        results = [
            self._shell.window_action(window_id, action)
            if window_id in known
            else ActionResult.NOT_FOUND
            for window_id in window_ids
        ]
        if ActionResult.FAILED in results:
            return ActionResult.FAILED
        if ActionResult.OK in results:
            return ActionResult.OK
        if ActionResult.UNSUPPORTED in results:
            return ActionResult.UNSUPPORTED
        return ActionResult.NOT_FOUND
