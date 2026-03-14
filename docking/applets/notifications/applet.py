"""GTK lifecycle glue for Notifications applet."""

from __future__ import annotations

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
from docking.applets.identity import AppletId
from docking.i18n import _

from .render import create_notifications_icon
from .state import (
    NotificationsBackend,
    NotificationsState,
    detect_backend,
    tooltip_text,
)

if TYPE_CHECKING:
    from docking.core.config import Config

POLL_INTERVAL_S = 2
ACTIVITY_WINDOW_S = 8
HISTORY_LIMIT = 40
TOOLTIP_APP_LIMIT = 36
TOOLTIP_SUMMARY_LIMIT = 72
TOOLTIP_BODY_LIMIT = 96
MAX_HISTORY_BADGE_COUNT = 99
ACTIVITY_MONITOR_TERMINATE_TIMEOUT_S = 0.4
ELLIPSIS_RESERVED_CHARS = 3


@dataclass(frozen=True, slots=True)
class NotificationEntry:
    app_name: str
    summary: str
    body: str


class NotificationsApplet(Applet):
    """Notification status and Do Not Disturb toggle."""

    id = AppletId.NOTIFICATIONS
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
        self._history: list[NotificationEntry] = []
        self._history_index: int = 0
        super().__init__(icon_size=icon_size, config=config)

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
            self.refresh_presentation()
            self._refresh_now()

    def on_scroll(self, direction_up: bool) -> None:
        if not self._history:
            return
        step = 1 if direction_up else -1
        self._history_index = (self._history_index + step) % len(self._history)
        self.refresh_presentation()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        if not self._state.available:
            placeholder = Gtk.MenuItem(label=_("No notification backend available"))
            placeholder.set_sensitive(False)
            return [placeholder]

        items: list[Gtk.MenuItem] = []

        dnd = Gtk.CheckMenuItem(label=_("Do Not Disturb"))
        dnd.set_active(self._state.paused)
        dnd.connect("toggled", self._on_toggle_dnd)
        items.append(dnd)

        if self._state.pending_known:
            pending = Gtk.MenuItem(
                label=_("Pending: {n}").format(n=self._state.pending)
            )
            pending.set_sensitive(False)
            items.append(pending)

        clear_history = Gtk.MenuItem(label=_("Clear History"))
        clear_history.connect("activate", lambda _w: self._on_clear_history())
        items.append(clear_history)

        if self._backend.supports_clear:
            clear = Gtk.MenuItem(label=_("Clear Notifications"))
            clear.connect("activate", lambda _w: self._on_clear())
            items.append(clear)

        return items

    def _on_toggle_dnd(self, widget: Gtk.CheckMenuItem) -> None:
        target = widget.get_active()
        if target == self._state.paused:
            return
        if self._backend.set_paused(target):
            self._state = replace(self._state, paused=target)
            self.refresh_presentation()
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
        self.refresh_presentation()

    def _tick(self) -> bool:
        threading.Thread(target=self._poll_worker, daemon=True).start()
        return True

    def _poll_worker(self) -> None:
        if not self._state.available:
            self._backend = detect_backend()
        state = self._backend.get_state()
        GLib.idle_add(self._on_poll_result, state)

    def _on_poll_result(self, state: NotificationsState) -> bool:
        if state != self._state:
            self._state = state
            self.refresh_presentation()
        return False

    def _refresh_now(self) -> None:
        self._on_poll_result(self._backend.get_state())

    def _show_activity_badge(self) -> bool:
        return (
            self._state.available
            and not self._state.pending_known
            and time.monotonic() < self._activity_until_monotonic
        )

    def _history_badge_count(self) -> int:
        return max(0, min(MAX_HISTORY_BADGE_COUNT, len(self._history)))

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
            self.refresh_presentation()
        return False

    def _on_activity_expired(self) -> bool:
        self._activity_clear_id = 0
        if self._show_activity_badge():
            return False
        self.refresh_presentation()
        return False

    def _start_activity_monitor(self) -> None:
        if self._activity_monitor_proc is not None:
            return
        if shutil.which("dbus-monitor") is None:
            return
        try:
            self._activity_monitor_proc = subprocess.Popen(
                [
                    "dbus-monitor",
                    "--session",
                    "interface='org.freedesktop.Notifications',member='Notify'",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError:
            self._activity_monitor_proc = None
            return

        self._activity_monitor_thread = threading.Thread(
            target=self._activity_monitor_worker,
            daemon=True,
        )
        self._activity_monitor_thread.start()

    def _stop_activity_monitor(self) -> None:
        proc = self._activity_monitor_proc
        self._activity_monitor_proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=ACTIVITY_MONITOR_TERMINATE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            return

    def _activity_monitor_worker(self) -> None:
        proc = self._activity_monitor_proc
        if proc is None or proc.stdout is None:
            return
        capture_notify = False
        notify_strings: list[str] = []
        try:
            for line in proc.stdout:
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
        except Exception:
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
        idx = max(0, min(self._history_index, len(self._history) - 1))
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
