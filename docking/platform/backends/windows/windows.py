"""Project native Windows application windows into the shared dock model."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from gi.repository import GLib

from docking.log import get_logger
from docking.platform.backends.base import ActionResult, WindowId
from docking.platform.backends.native_windows import NativeWindowService

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.base import WindowSnapshot
    from docking.platform.backends.windows.native import WindowsNativeAPI
    from docking.platform.model import DockModel

log = get_logger("windows.windows")


class WindowsWindowService(NativeWindowService):
    def __init__(
        self,
        *,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        config: Config | None = None,
        native: WindowsNativeAPI | None = None,
    ) -> None:
        super().__init__(
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
            config=config,
        )
        self._native = native
        self._timer = 0
        self._previews_available = False

    def start(self) -> None:
        if self._timer:
            return
        if self._native is None:
            from .native import WindowsNativeAPI

            self._native = WindowsNativeAPI()
        self.refresh()
        self._timer = GLib.timeout_add(500, self._poll)

    def stop(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        super().stop()

    def _poll(self) -> bool:
        self.refresh()
        return True

    def refresh(self) -> None:
        if self._native is None:
            return
        try:
            self.replace_windows(
                tuple(
                    replace(window, can_preview=self._previews_available)
                    for window in self._native.windows()
                )
            )
        except OSError:
            log.exception("Could not refresh native Windows application windows")

    def set_previews_available(self, available: bool) -> None:
        if self._previews_available != available:
            self._previews_available = available
            self.refresh()

    def _taskbar_window(self, window: WindowSnapshot) -> bool:
        # Virtual desktop membership is not implemented yet. An unsupported
        # workspace preference must not hide every application from the dock.
        return not window.skip_taskbar

    def perform(self, window_id: WindowId, action: str) -> ActionResult:
        window = self._snapshots.get(window_id)
        if self._native is None or window is None:
            return ActionResult.NOT_FOUND
        return self._native.perform(window, action)
