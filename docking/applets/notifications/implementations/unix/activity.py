"""Unix notification activity observation, including Flatpak host ownership."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import threading
from contextlib import suppress
from queue import Empty, Full, Queue

from docking.applets.notifications.state import NotificationEntry
from docking.log import get_logger, with_context
from docking.platform.environment import flatpak, is_flatpak

log = with_context(get_logger(name="notifications"), component="activity")

ACTIVITY_MONITOR_TERMINATE_TIMEOUT_S = 0.4
NOTIFICATION_MONITOR_RULE = "interface='org.freedesktop.Notifications',member='Notify'"
HOST_MONITOR_PID_PREFIX = "__DOCKING_HOST_DBUS_MONITOR_PID="


class UnixNotificationActivityMonitor:
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
