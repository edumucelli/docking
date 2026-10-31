"""Unix notification activity observation, including Flatpak host ownership."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable

import gi

from docking.applets.notifications.backend import NotificationActivityMonitor

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.log import get_logger, with_context
from docking.platform.environment import flatpak, is_flatpak

log = with_context(get_logger(name="notifications"), component="activity")
ACTIVITY_MONITOR_TERMINATE_TIMEOUT_S = 0.4
NOTIFICATION_MONITOR_RULE = "interface='org.freedesktop.Notifications',member='Notify'"
HOST_MONITOR_PID_PREFIX = "__DOCKING_HOST_DBUS_MONITOR_PID="


class UnixNotificationActivityMonitor(NotificationActivityMonitor):
    def __init__(
        self,
        *,
        on_event: Callable[[str, str, str], bool | None],
        on_activity: Callable[[], bool | None],
    ) -> None:
        self._on_event = on_event
        self._on_activity = on_activity
        self._activity_monitor_proc: subprocess.Popen[str] | None = None
        self._activity_monitor_thread: threading.Thread | None = None
        self._activity_monitor_host_pid: str | None = None

    def start(self) -> None:
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

    def stop(self) -> None:
        proc = self._activity_monitor_proc
        self._activity_monitor_proc = None
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
            proc.kill()
        except OSError as exc:
            log.debug("Failed to stop dbus-monitor cleanly: %s", exc)
            return

    def _stop_host_activity_monitor(self, pid: str) -> None:
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
                    self._activity_monitor_host_pid = line[
                        len(HOST_MONITOR_PID_PREFIX) :
                    ].strip()
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
                        GLib.idle_add(
                            self._on_event,
                            app_name,
                            summary,
                            body,
                        )
                    else:
                        GLib.idle_add(self._on_activity)
                    capture_notify = False
        except Exception as exc:
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
