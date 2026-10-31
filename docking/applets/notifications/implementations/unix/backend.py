"""Unix native operations extracted without changing fallback policy."""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from docking.applets.notifications.backend import (
    NotificationsOperations as NotificationsOperationsBase,
)
from docking.platform.environment import flatpak

from ...backend import NotificationsBackend
from ...state import (
    _GNOME_SCHEMA,
    _GNOME_SHOW_BANNERS_KEY,
    NotificationsState,
    _parse_bool,
    _parse_pending_count,
    log,
    unavailable_state,
)
from .activity import UnixNotificationActivityMonitor


def _run_direct(cmd: list[str], timeout_s: float = 2.0) -> str | None:
    """Run command and return stdout when successful."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.bind(action="run").debug("Command failed %s: %s", cmd, exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _run(cmd: list[str], timeout_s: float = 2.0) -> str | None:
    """Run host notification command when sandboxed, else run directly."""
    host_cmd = flatpak.host_command(cmd)
    if host_cmd is not None:
        output = _run_direct(host_cmd, timeout_s=timeout_s)
        if output is not None:
            return output
    return _run_direct(cmd, timeout_s=timeout_s)


class DunstBackend(NotificationsBackend):
    """Backend for dunstctl."""

    name = "dunstctl"
    supports_clear = True

    def get_state(self) -> NotificationsState:
        paused = _parse_bool(_run(["dunstctl", "is-paused"]))
        if paused is None:
            return unavailable_state()

        pending = _parse_pending_count(_run(["dunstctl", "count", "waiting"]))
        if pending is None:
            pending = _parse_pending_count(_run(["dunstctl", "count"]))

        return NotificationsState(
            available=True,
            backend=self.name,
            paused=paused,
            pending=pending or 0,
            pending_known=pending is not None,
        )

    def set_paused(self, paused: bool) -> bool:
        value = "true" if paused else "false"
        return _run(["dunstctl", "set-paused", value]) is not None

    def clear_notifications(self) -> bool:
        return _run(["dunstctl", "history-clear"]) is not None


class GnomeBackend(NotificationsBackend):
    """Backend for GNOME notification settings via gsettings."""

    name = "gnome"
    supports_clear = False

    def get_state(self) -> NotificationsState:
        show_banners = _parse_bool(
            _run(["gsettings", "get", _GNOME_SCHEMA, _GNOME_SHOW_BANNERS_KEY])
        )
        if show_banners is None:
            return unavailable_state()
        return NotificationsState(
            available=True,
            backend=self.name,
            paused=not show_banners,
            pending=0,
            pending_known=False,
        )

    def set_paused(self, paused: bool) -> bool:
        show_banners = "false" if paused else "true"
        return (
            _run(
                [
                    "gsettings",
                    "set",
                    _GNOME_SCHEMA,
                    _GNOME_SHOW_BANNERS_KEY,
                    show_banners,
                ]
            )
            is not None
        )

    def clear_notifications(self) -> bool:
        return False


class NullBackend(NotificationsBackend):
    """Unavailable backend."""

    name = "none"
    supports_clear = False

    def get_state(self) -> NotificationsState:
        return unavailable_state()

    def set_paused(self, paused: bool) -> bool:
        _ = paused
        return False

    def clear_notifications(self) -> bool:
        return False


def detect_backend() -> NotificationsBackend:
    """Detect the best available notification backend."""
    if flatpak.host_command_available("dunstctl"):
        backend = DunstBackend()
        if backend.get_state().available:
            return backend
    if flatpak.host_command_available("gsettings"):
        backend = GnomeBackend()
        if backend.get_state().available:
            return backend
    return NullBackend()


class NotificationsOperations(NotificationsOperationsBase):
    def detect_backend(self) -> NotificationsBackend:
        return detect_backend()

    def activity_monitor(
        self,
        *,
        on_event: Callable[[str, str, str], bool | None],
        on_activity: Callable[[], bool | None],
    ) -> UnixNotificationActivityMonitor:
        return UnixNotificationActivityMonitor(
            on_event=on_event, on_activity=on_activity
        )
