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

"""Unix notification settings and bounded D-Bus activity monitoring."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import threading
from contextlib import suppress
from queue import Empty, Full, Queue

from docking.applets.notifications import meta
from docking.log import get_logger, with_context
from docking.platform.environment import flatpak, is_flatpak

from .backend import NotificationsBackend
from .state import (
    NotificationEntry,
    NotificationsSnapshot,
    NotificationsState,
    unavailable_state,
)

log = with_context(get_logger(name="notifications"), applet_id=meta.id)
_GNOME_SCHEMA = "org.gnome.desktop.notifications"
_GNOME_SHOW_BANNERS_KEY = "show-banners"


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
    except json.JSONDecodeError as exc:
        log.debug("Failed to parse pending notification count %r: %s", stripped, exc)
        return None

    if isinstance(payload, dict):
        waiting = payload.get("waiting")
        if isinstance(waiting, int):
            return max(0, waiting)
    return None


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
            paused_known=True,
            can_pause=True,
            can_clear=True,
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
            paused_known=True,
            can_pause=True,
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


def detect_backend() -> DunstBackend | GnomeBackend | NullBackend:
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


ACTIVITY_MONITOR_TERMINATE_TIMEOUT_S = 0.4
NOTIFICATION_MONITOR_RULE = "interface='org.freedesktop.Notifications',member='Notify'"
HOST_MONITOR_PID_PREFIX = "__DOCKING_HOST_DBUS_MONITOR_PID="


class ActivityMonitor:
    def __init__(self):
        self._activity_monitor_proc: subprocess.Popen[str] | None = None
        self._activity_monitor_thread: threading.Thread | None = None
        self._activity_monitor_host_pid: str | None = None
        self._events: Queue[NotificationEntry | None] = Queue(maxsize=128)
        self._closing = threading.Event()
        self._monitor_lock = threading.Lock()

    def drain(self) -> tuple[NotificationEntry | None, ...]:
        events = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except Empty:
                return tuple(events)

    def _start_activity_monitor(self) -> None:
        if self._activity_monitor_proc is not None:
            return
        command = self._activity_monitor_command()
        if command is None:
            log.bind(action="activity_monitor").warning(
                "dbus-monitor not found; notification activity monitoring is disabled"
            )
            return
        try:
            self._activity_monitor_proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self._activity_monitor_proc = None
            log.bind(action="activity_monitor").warning(
                "Failed to start dbus-monitor: %s", exc
            )
            return

        self._activity_monitor_thread = threading.Thread(
            target=self._activity_monitor_worker,
            daemon=True,
        )
        self._activity_monitor_thread.start()

    def _activity_monitor_command(self) -> list[str] | None:
        if is_flatpak():
            # The Flatpak D-Bus proxy does not expose other clients' Notify
            # method calls to sandbox dbus-monitor, so observe on the host.
            monitor_rule = shlex.quote(NOTIFICATION_MONITOR_RULE)
            return flatpak.host_command(
                [
                    "sh",
                    "-c",
                    (
                        "command -v dbus-monitor >/dev/null || exit 127; "
                        f"echo {HOST_MONITOR_PID_PREFIX}$$; "
                        f"exec dbus-monitor --session {monitor_rule}"
                    ),
                ]
            )

        dbus_monitor = shutil.which("dbus-monitor")
        if dbus_monitor is None:
            return None
        return [dbus_monitor, "--session", NOTIFICATION_MONITOR_RULE]

    def _stop_activity_monitor(self) -> None:
        proc = self._activity_monitor_proc
        self._activity_monitor_proc = None
        with self._monitor_lock:
            self._closing.set()
            host_pid = self._activity_monitor_host_pid
            self._activity_monitor_host_pid = None
        if proc is None:
            return
        if host_pid is not None:
            self._stop_host_activity_monitor(host_pid)
        try:
            proc.terminate()
            proc.wait(timeout=ACTIVITY_MONITOR_TERMINATE_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            log.debug("dbus-monitor did not exit cleanly, forcing kill: %s", exc)
            try:
                proc.kill()
                proc.wait(timeout=ACTIVITY_MONITOR_TERMINATE_TIMEOUT_S)
            except (OSError, subprocess.TimeoutExpired) as kill_error:
                log.warning("Could not reap dbus-monitor: %s", kill_error)
        except OSError as exc:
            log.debug("Failed to stop dbus-monitor cleanly: %s", exc)
            return

    def _stop_host_activity_monitor(self, pid: str) -> None:
        if not pid.isdecimal() or int(pid) <= 0:
            return
        command = flatpak.host_command(["kill", pid], sanitize_env=False)
        if command is None:
            return
        try:
            subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=ACTIVITY_MONITOR_TERMINATE_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("Failed to stop host dbus-monitor %s: %s", pid, exc)

    def _activity_monitor_worker(self) -> None:
        proc = self._activity_monitor_proc
        if proc is None or proc.stdout is None:
            return
        capture_notify = False
        notify_strings: list[str] = []
        try:
            for line in proc.stdout:
                if line.startswith(HOST_MONITOR_PID_PREFIX):
                    # Store the host-side pid so stop() can kill the actual
                    # dbus-monitor, not just the flatpak-spawn wrapper.
                    pid = line[len(HOST_MONITOR_PID_PREFIX) :].strip()
                    with self._monitor_lock:
                        closing = self._closing.is_set()
                        if not closing:
                            self._activity_monitor_host_pid = pid
                    if closing:
                        self._stop_host_activity_monitor(pid)
                    continue
                if "member=Notify" in line:
                    capture_notify = True
                    notify_strings = []
                    continue
                if not capture_notify:
                    continue

                value = self._extract_monitor_string(line)
                if value is not None:
                    notify_strings.append(value)

                stripped = line.strip()
                if stripped.startswith(("array [", "int32 ")):
                    if len(notify_strings) >= 4:
                        app_name = notify_strings[0]
                        summary = notify_strings[2]
                        body = notify_strings[3]
                        self._record(NotificationEntry(app_name, summary, body))
                    else:
                        self._record(None)
                    capture_notify = False
        except (OSError, UnicodeError) as exc:
            log.bind(action="activity_monitor").warning(
                "Notification activity monitor stopped unexpectedly: %s", exc
            )
            return

    @staticmethod
    def _extract_monitor_string(line: str) -> str | None:
        stripped = line.strip()
        prefix = 'string "'
        if not stripped.startswith(prefix) or not stripped.endswith('"'):
            return None
        return stripped[len(prefix) : -1].replace('\\"', '"')

    def _record(self, entry: NotificationEntry | None) -> None:
        try:
            self._events.put_nowait(entry)
        except Full:
            # Keep the most recent activity, bounded even when GTK is stalled.
            with suppress(Empty):
                self._events.get_nowait()
            self._events.put_nowait(entry)


class LinuxNotificationsBackend(NotificationsBackend):
    def __init__(self) -> None:
        self._backend: DunstBackend | GnomeBackend | NullBackend = NullBackend()
        self._monitor = ActivityMonitor()
        self._stopped = threading.Event()
        self._started = False

    def poll(self) -> NotificationsSnapshot:
        if self._stopped.is_set():
            return NotificationsSnapshot()
        if not self._started:
            self._monitor._start_activity_monitor()
            self._started = True
        state = self._backend.get_state()
        if not state.available:
            self._backend = detect_backend()
            state = self._backend.get_state()
        return NotificationsSnapshot(state, self._monitor.drain())

    def set_paused(self, paused: bool) -> bool:
        return not self._stopped.is_set() and self._backend.set_paused(paused)

    def clear_notifications(self, state: NotificationsState) -> bool:
        return not self._stopped.is_set() and self._backend.clear_notifications()

    def stop(self) -> None:
        self._stopped.set()

    def close(self) -> None:
        self._monitor._stop_activity_monitor()
