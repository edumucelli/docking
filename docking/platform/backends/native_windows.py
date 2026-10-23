"""Shared taskbar projection and actions for compositor-owned snapshots."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from docking.platform.applications.matcher import AppIdMatcher
from docking.platform.applications.running import RunningAppInfo, RunningWindowInfo
from docking.platform.backends.base import (
    ActionResult,
    WindowId,
    WindowService,
    WindowSnapshot,
)
from docking.platform.backends.diagnostics import WindowTrackingDiagnostic
from docking.platform.backends.visibility import WindowChanges

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel


class NativeWindowService(WindowService):
    """Compositors supply state and action execution; app identity stays shared."""

    def __init__(
        self,
        *,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        config: Config | None = None,
    ) -> None:
        self._model = model
        self._config = config
        self._matcher = AppIdMatcher(
            registry=application_registry,
            process_identity_service=process_identity_service,
        )
        self._snapshots: dict[WindowId, WindowSnapshot] = {}
        self._changes = WindowChanges()

    def start(self) -> None:
        self.refresh()

    def stop(self) -> None:
        self.replace_windows(())
        self._changes.clear()

    def replace_windows(self, windows: Sequence[WindowSnapshot]) -> None:
        self._matcher.sync_visible_items(self._model.visible_items())
        snapshots: dict[WindowId, WindowSnapshot] = {}
        running: dict[str, list[RunningWindowInfo]] = {}
        for window in windows:
            match = self._matcher.match_result(
                window.app_id or window.wm_class or "", process_id=window.pid
            )
            desktop_id = match.desktop_id if match is not None else ""
            window = replace(window, desktop_id=desktop_id)
            snapshots[window.id] = window
            if desktop_id and self._taskbar_window(window):
                running.setdefault(desktop_id, []).append(
                    RunningWindowInfo(
                        desktop_id=desktop_id,
                        xid=0,
                        window_id=window.id,
                        active=window.active,
                        urgent=window.urgent,
                        window=window.id,
                        runtime_app=match.runtime_app if match is not None else None,
                    )
                )
        self._snapshots = snapshots
        self._model.update_running(
            running={
                key: RunningAppInfo.from_windows(rows) for key, rows in running.items()
            }
        )
        self._changes.notify()

    def watch(self, on_change: Callable[[], None]) -> object:
        return self._changes.watch(on_change)

    def unwatch(self, handle: object) -> None:
        self._changes.unwatch(handle)

    def diagnostic_snapshot(self) -> WindowTrackingDiagnostic:
        return WindowTrackingDiagnostic(
            status="available", detail="Native compositor snapshots."
        )

    def list_all_windows(self) -> Sequence[WindowSnapshot]:
        return tuple(self._snapshots.values())

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return tuple(
            row
            for row in self._snapshots.values()
            if row.desktop_id == desktop_id and self._taskbar_window(row)
        )

    def _taskbar_window(self, window: WindowSnapshot) -> bool:
        return not window.skip_taskbar and (
            self._config is None
            or not self._config.current_workspace_only
            or window.on_current_workspace is True
        )

    def list_preview_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return self.list_windows(desktop_id)

    def icon_name_for_desktop(self, desktop_id: str) -> str:
        return "application-x-executable"

    def perform(self, window_id: WindowId, action: str) -> ActionResult:
        raise NotImplementedError

    def _act(self, window_id: WindowId, action: str) -> ActionResult:
        window = self._snapshots.get(window_id)
        if window is None:
            return ActionResult.NOT_FOUND
        supported = {
            "activate": window.can_activate,
            "minimize": window.can_minimize,
            "close": window.can_close,
        }
        if not supported.get(action, False):
            return ActionResult.UNSUPPORTED
        return self.perform(window_id, action)

    def activate(self, window_id: WindowId) -> ActionResult:
        return self._act(window_id, "activate")

    def _first(self, desktop_id: str) -> WindowSnapshot | None:
        rows = self.list_windows(desktop_id)
        return next((row for row in rows if row.active), rows[0] if rows else None)

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        row = self._first(desktop_id)
        return self.activate(row.id) if row else ActionResult.NOT_FOUND

    def cycle(self, desktop_id: str) -> ActionResult:
        rows = self.list_windows(desktop_id)
        if not rows:
            return ActionResult.NOT_FOUND
        index = next((index for index, row in enumerate(rows) if row.active), -1)
        return self.activate(rows[(index + 1) % len(rows)].id)

    def _all(self, desktop_id: str, action: str) -> ActionResult:
        rows = self.list_windows(desktop_id)
        if not rows:
            return ActionResult.NOT_FOUND
        results = [self._act(row.id, action) for row in rows]
        return next(
            (result for result in results if result is not ActionResult.OK),
            ActionResult.OK,
        )

    def minimize_all(self, desktop_id: str) -> ActionResult:
        return self._all(desktop_id, "minimize")

    def close(self, window_id: WindowId) -> ActionResult:
        return self._act(window_id, "close")

    def close_all(self, desktop_id: str) -> ActionResult:
        return self._all(desktop_id, "close")

    def close_focused(self, desktop_id: str) -> ActionResult:
        row = self._first(desktop_id)
        return self.close(row.id) if row else ActionResult.NOT_FOUND

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        row = self._first(desktop_id)
        if row is None:
            return ActionResult.NOT_FOUND
        if row.active and row.can_minimize:
            return self.minimize_all(desktop_id)
        return self.activate(row.id)
