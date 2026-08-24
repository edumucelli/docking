"""Read-only window tracking through Muffin's session-bus snapshot API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger
from docking.platform.applications.matcher import AppIdMatcher
from docking.platform.applications.running import RunningAppInfo, RunningWindowInfo
from docking.platform.applications.types import ApplicationMatch
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    Rect,
    WindowId,
    WindowService,
    WindowSnapshot,
)

if TYPE_CHECKING:
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel

log = get_logger(name="backend.cinnamon.muffin")

BUS_NAME = "org.cinnamon.Muffin.Debug"
OBJECT_PATH = "/org/cinnamon/Muffin/Debug"
INTERFACE = "org.cinnamon.Muffin.Debug"


class MuffinDebugClient:
    """Client for Muffin's read-only ListWindows method."""

    def __init__(self, *, proxy: Gio.DBusProxy) -> None:
        self._proxy = proxy

    @classmethod
    def connect(cls) -> MuffinDebugClient | None:
        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                None,
            )
            proxy.call_sync(
                "ListWindows",
                None,
                Gio.DBusCallFlags.NO_AUTO_START,
                1000,
                None,
            )
        except Exception as exc:
            log.info("Muffin window snapshot API unavailable: %s", exc)
            return None
        return cls(proxy=proxy)

    def list_windows(self) -> Sequence[Mapping[str, Any]]:
        try:
            result = self._proxy.call_sync(
                "ListWindows",
                None,
                Gio.DBusCallFlags.NO_AUTO_START,
                1000,
                None,
            )
            rows = result.unpack()[0]
        except Exception as exc:
            log.warning("Muffin ListWindows failed: %s", exc)
            return ()
        return tuple(row for row in rows if isinstance(row, Mapping))


@dataclass(frozen=True)
class _MuffinWindow:
    muffin_id: int
    title: str
    app_id: str
    application_match: ApplicationMatch | None
    active: bool
    urgent: bool
    geometry: Rect | None
    workspace_id: str | None
    pid: int | None

    @property
    def window_id(self) -> WindowId:
        return WindowId(
            backend=DisplayServer.WAYLAND,
            value=f"muffin:{self.muffin_id}",
        )

    @property
    def desktop_id(self) -> str | None:
        return (
            self.application_match.desktop_id
            if self.application_match is not None
            else None
        )


class MuffinWindowService(WindowService):
    """Poll Muffin snapshots without claiming unsupported window actions."""

    def __init__(
        self,
        *,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        client: MuffinDebugClient,
    ) -> None:
        self._model = model
        self._matcher = AppIdMatcher(
            registry=application_registry,
            process_identity_service=process_identity_service,
        )
        self._client = client
        self._windows: dict[int, _MuffinWindow] = {}
        self._poll_source_id = 0

    def start(self) -> None:
        self.refresh()
        self._poll_source_id = GLib.timeout_add_seconds(2, self._poll)

    def stop(self) -> None:
        if self._poll_source_id:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = 0
        self._windows.clear()
        self._model.update_running(running={})

    def refresh(self) -> None:
        self._matcher.sync_visible_items(self._model.visible_items())
        windows: dict[int, _MuffinWindow] = {}
        for row in self._client.list_windows():
            window = self._window_from_row(row)
            if window is not None and not _bool(row, "skip-taskbar"):
                windows[window.muffin_id] = window
        self._windows = windows
        self._publish_running()

    def list_all_windows(self) -> Sequence[WindowSnapshot]:
        return tuple(self._snapshot(window) for window in self._windows.values())

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return tuple(
            self._snapshot(window)
            for window in self._windows.values()
            if window.desktop_id == desktop_id
        )

    def list_preview_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return self.list_windows(desktop_id)

    def icon_name_for_desktop(self, desktop_id: str) -> str:
        return "application-x-executable"

    def activate(self, window_id: WindowId) -> ActionResult:
        return self._unsupported_or_missing(window_id)

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        return self._unsupported_or_empty(desktop_id)

    def cycle(self, desktop_id: str) -> ActionResult:
        return self._unsupported_or_empty(desktop_id)

    def minimize_all(self, desktop_id: str) -> ActionResult:
        return self._unsupported_or_empty(desktop_id)

    def close(self, window_id: WindowId) -> ActionResult:
        return self._unsupported_or_missing(window_id)

    def close_all(self, desktop_id: str) -> ActionResult:
        return self._unsupported_or_empty(desktop_id)

    def close_focused(self, desktop_id: str) -> ActionResult:
        return self._unsupported_or_empty(desktop_id)

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        return self._unsupported_or_empty(desktop_id)

    def _poll(self) -> bool:
        self.refresh()
        return True

    def _window_from_row(self, row: Mapping[str, Any]) -> _MuffinWindow | None:
        muffin_id = _int(row, "id")
        if muffin_id is None:
            return None
        identities = tuple(
            dict.fromkeys(
                value
                for key in (
                    "app-id",
                    "gtk-application-id",
                    "sandboxed-app-id",
                    "wm-class",
                    "wm-class-instance",
                )
                if (value := _text(row, key))
            )
        )
        app_id = identities[0] if identities else ""
        pid = _int(row, "pid")
        if pid is not None and pid <= 0:
            pid = None
        match = None
        for identity in identities:
            match = self._matcher.match_result(identity, process_id=pid)
            if match is not None:
                break
        return _MuffinWindow(
            muffin_id=muffin_id,
            title=_text(row, "title") or "Window",
            app_id=app_id,
            application_match=match,
            active=_bool(row, "focused"),
            urgent=_bool(row, "demands-attention"),
            geometry=_rect(row.get("frame-rect")),
            workspace_id=(
                str(workspace)
                if (workspace := _int(row, "workspace")) is not None
                else None
            ),
            pid=pid,
        )

    def _publish_running(self) -> None:
        grouped: dict[str, list[RunningWindowInfo]] = {}
        for window in self._windows.values():
            if window.desktop_id is None:
                continue
            grouped.setdefault(window.desktop_id, []).append(
                RunningWindowInfo(
                    desktop_id=window.desktop_id,
                    xid=window.muffin_id,
                    window_id=window.window_id,
                    active=window.active,
                    urgent=window.urgent,
                    window=window.muffin_id,
                    runtime_app=(
                        window.application_match.runtime_app
                        if window.application_match is not None
                        else None
                    ),
                )
            )
        self._model.update_running(
            running={
                desktop_id: RunningAppInfo.from_windows(items)
                for desktop_id, items in grouped.items()
            }
        )

    @staticmethod
    def _snapshot(window: _MuffinWindow) -> WindowSnapshot:
        return WindowSnapshot(
            id=window.window_id,
            desktop_id=window.desktop_id or "",
            title=window.title,
            app_id=window.app_id or None,
            active=window.active,
            urgent=window.urgent,
            geometry=window.geometry,
            workspace_id=window.workspace_id,
        )

    def _unsupported_or_missing(self, window_id: WindowId) -> ActionResult:
        if not any(window.window_id == window_id for window in self._windows.values()):
            return ActionResult.NOT_FOUND
        return ActionResult.UNSUPPORTED

    def _unsupported_or_empty(self, desktop_id: str) -> ActionResult:
        if not any(
            window.desktop_id == desktop_id for window in self._windows.values()
        ):
            return ActionResult.NOT_FOUND
        return ActionResult.UNSUPPORTED


def _unpack(value: Any) -> Any:
    unpack = getattr(value, "unpack", None)
    return unpack() if callable(unpack) else value


def _text(row: Mapping[str, Any], key: str) -> str:
    value = _unpack(row.get(key, ""))
    return str(value).strip() if value is not None else ""


def _int(row: Mapping[str, Any], key: str) -> int | None:
    try:
        return int(_unpack(row.get(key)))
    except (TypeError, ValueError):
        return None


def _bool(row: Mapping[str, Any], key: str) -> bool:
    return bool(_unpack(row.get(key, False)))


def _rect(value: Any) -> Rect | None:
    value = _unpack(value)
    if not isinstance(value, tuple | list) or len(value) != 4:
        return None
    try:
        x, y, width, height = (int(part) for part in value)
    except (TypeError, ValueError):
        return None
    return Rect(x=x, y=y, width=width, height=height)
