"""State and backend helpers for the Notifications applet."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol

from docking.applets.notifications import meta
from docking.i18n import _
from docking.log import get_logger, with_context

_log = with_context(
    get_logger(name="notifications"),
    applet_id=meta.id,
)

_GNOME_SCHEMA = "org.gnome.desktop.notifications"
_GNOME_SHOW_BANNERS_KEY = "show-banners"


@dataclass(frozen=True, slots=True)
class NotificationsState:
    """Current notification backend state."""

    available: bool = False
    backend: str = ""
    paused: bool = False
    pending: int = 0
    pending_known: bool = False


def unavailable_state() -> NotificationsState:
    """Canonical unavailable state."""
    return NotificationsState()


def tooltip_text(state: NotificationsState) -> str:
    """Tooltip text for notifications applet."""
    if not state.available:
        return _("Notifications: No backend available")

    lines: list[str] = []
    if state.pending_known:
        lines.append(_("Pending: {n}").format(n=state.pending))
    if not lines:
        lines.append(_("Notifications"))
    return "\n".join(lines)


def pending_badge_count(state: NotificationsState) -> int:
    """Badge count shown on icon."""
    if not state.available or not state.pending_known:
        return 0
    return max(0, min(99, state.pending))


def _run(cmd: list[str], timeout_s: float = 2.0) -> str | None:
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
        _log.bind(action="run").debug("Command failed %s: %s", cmd, exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    return None


def _parse_pending_count(value: str | None) -> int | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None

    if stripped.isdigit():
        return int(stripped)

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None

    if isinstance(payload, dict):
        waiting = payload.get("waiting")
        if isinstance(waiting, int):
            return max(0, waiting)
    return None


def _has_command(command: str) -> bool:
    return shutil.which(command) is not None


class NotificationsBackend(Protocol):
    """Protocol for backend adapters."""

    name: str
    supports_clear: bool

    def get_state(self) -> NotificationsState: ...

    def set_paused(self, paused: bool) -> bool: ...

    def clear_notifications(self) -> bool: ...


class DunstBackend:
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


class GnomeBackend:
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


class NullBackend:
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
    if _has_command("dunstctl"):
        backend = DunstBackend()
        if backend.get_state().available:
            return backend
    if _has_command("gsettings"):
        backend = GnomeBackend()
        if backend.get_state().available:
            return backend
    return NullBackend()
