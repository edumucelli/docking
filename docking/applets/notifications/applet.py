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

"""GTK lifecycle glue for Notifications applet."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.notifications import meta
from docking.applets.worker import BackgroundWorker
from docking.core.math import clamp_index, clamp_int
from docking.i18n import _
from docking.log import get_logger, with_context
from docking.platform.environment import flatpak, is_flatpak

from .render import create_notifications_icon
from .state import (
    NotificationsBackend,
    NotificationsState,
    detect_backend,
    tooltip_text,
)

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="notifications"), applet_id=meta.id)

POLL_INTERVAL_S = 2
ACTIVITY_WINDOW_S = 8
HISTORY_LIMIT = 40
TOOLTIP_APP_LIMIT = 36
TOOLTIP_SUMMARY_LIMIT = 72
TOOLTIP_BODY_LIMIT = 96
MAX_HISTORY_BADGE_COUNT = 99
ACTIVITY_MONITOR_TERMINATE_TIMEOUT_S = 0.4
ELLIPSIS_RESERVED_CHARS = 3
NOTIFICATION_MONITOR_RULE = "interface='org.freedesktop.Notifications',member='Notify'"
HOST_MONITOR_PID_PREFIX = "__DOCKING_HOST_DBUS_MONITOR_PID="


@dataclass(frozen=True, slots=True)
class NotificationEntry:
    app_name: str
    summary: str
    body: str


class NotificationsApplet(Applet):
    """Notification status and Do Not Disturb toggle."""

    id = meta.id
    name = _("Notifications")
    icon_name = "preferences-system-notifications"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._backend: NotificationsBackend = detect_backend()
        self._state: NotificationsState = self._backend.get_state()
        self._timer_id: int = 0
        self._activity_until_monotonic: float = 0.0
        self._activity_clear_id: int = 0
        self._activity_monitor_proc: subprocess.Popen[str] | None = None
        self._activity_monitor_thread: threading.Thread | None = None
        self._activity_monitor_host_pid: str | None = None
        self._history: list[NotificationEntry] = []
        self._history_index: int = 0
        self._worker = BackgroundWorker()
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int):
        return create_notifications_icon(
            size=size,
            available=self._state.available,
            paused=self._state.paused,
            badge_count=self._history_badge_count(),
            activity=self._show_activity_badge(),
        )

    def refresh_tooltip(self) -> None:
        lines = [tooltip_text(self._state)]
        current = self._current_notification_lines()
        if current:
            lines.append("")
            lines.extend(current)
        self.item.name = "\n".join(lines)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)
        self._start_activity_monitor()

    def stop(self) -> None:
        if self._activity_clear_id:
            GLib.source_remove(self._activity_clear_id)
            self._activity_clear_id = 0
        self._stop_activity_monitor()
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        if not self._state.available:
            return
        target = not self._state.paused
        if self._backend.set_paused(target):
            self._state = replace(self._state, paused=target)
            self.present()
            self._refresh_now()

    def on_scroll(self, direction_up: bool) -> None:
        if not self._history:
            return
        step = 1 if direction_up else -1
        self._history_index = (self._history_index + step) % len(self._history)
        self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        if not self._state.available:
            return [disabled_menu_item(_("No notification backend available"), gtk=Gtk)]

        dnd = Gtk.CheckMenuItem(label=_("Do Not Disturb"))
        dnd.set_active(self._state.paused)
        dnd.connect("toggled", self._on_toggle_dnd)

        status: list[Gtk.MenuItem] = []
        if self._state.pending_known:
            status.append(
                disabled_menu_item(
                    _("Pending: {n}").format(n=self._state.pending),
                    gtk=Gtk,
                )
            )

        clear_history = Gtk.MenuItem(label=_("Clear History"))
        clear_history.connect("activate", lambda _w: self._on_clear_history())
        destructive = [clear_history]

        if self._backend.supports_clear:
            clear = Gtk.MenuItem(label=_("Clear Notifications"))
            clear.connect("activate", lambda _w: self._on_clear())
            destructive.append(clear)

        return menu_sections(status=status, display=[dnd], destructive=destructive)

    def _on_toggle_dnd(self, widget: Gtk.CheckMenuItem) -> None:
        target = widget.get_active()
        if target == self._state.paused:
            return
        if self._backend.set_paused(target):
            self._state = replace(self._state, paused=target)
            self.present()
            self._refresh_now()
            return
        widget.set_active(self._state.paused)

    def _on_clear(self) -> None:
        if self._backend.clear_notifications():
            self._refresh_now()

    def _on_clear_history(self) -> None:
        if not self._history:
            return
        self._history.clear()
        self._history_index = 0
        self.present()

    def _tick(self) -> bool:
        self._worker.run_guarded(
            key="poll",
            name="notifications-poll",
            fn=self._poll_worker,
            on_result=self._on_poll_result,
        )
        return True

    def _poll_worker(self) -> NotificationsState:
        if not self._state.available:
            self._backend = detect_backend()
        return self._backend.get_state()

    def _on_poll_result(self, state: NotificationsState) -> bool:
        if state != self._state:
            self._state = state
            self.present()
        return False

    def _refresh_now(self) -> None:
        self._on_poll_result(self._poll_worker())

    def _show_activity_badge(self) -> bool:
        return (
            self._state.available
            and not self._state.pending_known
            and time.monotonic() < self._activity_until_monotonic
        )

    def _history_badge_count(self) -> int:
        return clamp_int(len(self._history), 0, MAX_HISTORY_BADGE_COUNT)

    def _on_notification_activity(self, force_refresh: bool = False) -> bool:
        previous = self._show_activity_badge()
        self._activity_until_monotonic = time.monotonic() + ACTIVITY_WINDOW_S
        current = self._show_activity_badge()
        if self._activity_clear_id:
            GLib.source_remove(self._activity_clear_id)
        self._activity_clear_id = GLib.timeout_add_seconds(
            ACTIVITY_WINDOW_S + 1,
            self._on_activity_expired,
        )
        if force_refresh or current != previous:
            self.present()
        return False

    def _on_activity_expired(self) -> bool:
        self._activity_clear_id = 0
        if self._show_activity_badge():
            return False
        self.present()
        return False

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
                    "command -v dbus-monitor >/dev/null || exit 127; "
                    f"echo {HOST_MONITOR_PID_PREFIX}$$; "
                    f"exec dbus-monitor --session {monitor_rule}",
                ]
            )

        dbus_monitor = shutil.which("dbus-monitor")
        if dbus_monitor is None:
            return None
        return [dbus_monitor, "--session", NOTIFICATION_MONITOR_RULE]

    def _stop_activity_monitor(self) -> None:
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
                            self._on_notification_event,
                            app_name,
                            summary,
                            body,
                        )
                    else:
                        GLib.idle_add(self._on_notification_activity)
                    capture_notify = False
        except Exception as exc:
            log.bind(action="activity_monitor").warning(
                "Notification activity monitor stopped unexpectedly: %s", exc
            )
            return

    def _on_notification_event(self, app_name: str, summary: str, body: str) -> bool:
        app = self._shorten_for_tooltip(app_name, TOOLTIP_APP_LIMIT)
        title = self._shorten_for_tooltip(summary, TOOLTIP_SUMMARY_LIMIT)
        message = self._shorten_for_tooltip(body, TOOLTIP_BODY_LIMIT)
        entry = NotificationEntry(app_name=app, summary=title, body=message)
        self._history.insert(0, entry)
        if len(self._history) > HISTORY_LIMIT:
            self._history = self._history[:HISTORY_LIMIT]
        self._history_index = 0
        return self._on_notification_activity(force_refresh=True)

    def _current_notification_lines(self) -> list[str]:
        if not self._history:
            return []
        idx = clamp_index(self._history_index, len(self._history))
        entry = self._history[idx]
        lines = [f"Notification {idx + 1}/{len(self._history)}:"]
        if entry.summary:
            lines.append(entry.summary)
        if entry.body:
            lines.append(entry.body)
        if entry.app_name:
            lines.append(f"App: {entry.app_name}")
        return lines

    @staticmethod
    def _extract_monitor_string(line: str) -> str | None:
        stripped = line.strip()
        prefix = 'string "'
        if not stripped.startswith(prefix) or not stripped.endswith('"'):
            return None
        return stripped[len(prefix) : -1].replace('\\"', '"')

    @staticmethod
    def _shorten_for_tooltip(text: str, limit: int) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(1, limit - ELLIPSIS_RESERVED_CHARS)].rstrip() + "..."
