"""Desktop notification bridge for WebKit notifications."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger

log = get_logger("whatsapp.notifications")

NOTIFICATION_SERVICE = "org.freedesktop.Notifications"
NOTIFICATION_PATH = "/org/freedesktop/Notifications"
NOTIFICATION_IFACE = "org.freedesktop.Notifications"
METHOD_TIMEOUT_MS = 1500
NOTIFICATION_APP_NAME = "WhatsApp in Docking"


class DesktopNotifier:
    """Publish WebKit notifications and route clicks back to the web page."""

    def __init__(
        self,
        *,
        on_activate: Callable[[], None],
        on_shown: Callable[[], None],
    ) -> None:
        self._on_activate = on_activate
        self._on_shown = on_shown
        self._bus: Gio.DBusConnection | None = None
        self._action_subscription = 0
        self._closed_subscription = 0
        self._notifications: dict[int, Any] = {}
        self._notification_ids: dict[int, int] = {}
        self._tag_ids: dict[str, int] = {}
        self._stopped = False
        self._generation = 0

    def show(self, notification: Any) -> bool:
        """Send one WebKit notification to the desktop notification daemon."""
        try:
            bus = self._connection()
            title = str(notification.get_title() or "WhatsApp")
            body = str(notification.get_body() or "")
            tag = str(notification.get_tag() or "")
        except Exception as exc:
            log.debug("Could not prepare WhatsApp notification: %s", exc)
            return False

        replaces_id = self._tag_ids.get(tag, 0) if tag else 0
        hints = {
            "category": GLib.Variant("s", "im.received"),
            "desktop-entry": GLib.Variant("s", "org.docking.Docking"),
        }
        parameters = GLib.Variant(
            "(susssasa{sv}i)",
            (
                NOTIFICATION_APP_NAME,
                replaces_id,
                "org.docking.Docking",
                title,
                body,
                ["default", "Open"],
                hints,
                5000,
            ),
        )
        try:
            bus.call(
                NOTIFICATION_SERVICE,
                NOTIFICATION_PATH,
                NOTIFICATION_IFACE,
                "Notify",
                parameters,
                GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE,
                METHOD_TIMEOUT_MS,
                None,
                self._on_notify_finished,
                (notification, tag, self._generation),
            )
            notification.connect("closed", self._on_web_notification_closed)
            self._on_shown()
        except Exception as exc:
            log.debug("Could not publish WhatsApp notification: %s", exc)
            return False
        return True

    def clear(self) -> None:
        """Close notifications owned by this bridge and discard their state."""
        self._generation += 1
        notifications = tuple(self._notifications.items())
        self._notifications.clear()
        self._notification_ids.clear()
        self._tag_ids.clear()
        for notification_id, notification in notifications:
            self._close_desktop_notification(notification_id)
            with suppress(Exception):
                notification.close()

    def stop(self) -> None:
        """Release signal subscriptions and notification references."""
        self._stopped = True
        self._generation += 1
        if self._bus is not None:
            for subscription in (
                self._action_subscription,
                self._closed_subscription,
            ):
                if subscription:
                    self._bus.signal_unsubscribe(subscription)
        self._action_subscription = 0
        self._closed_subscription = 0
        self._notifications.clear()
        self._notification_ids.clear()
        self._tag_ids.clear()
        self._bus = None

    def _connection(self) -> Gio.DBusConnection:
        if self._bus is not None:
            return self._bus
        self._stopped = False
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._action_subscription = self._bus.signal_subscribe(
            NOTIFICATION_SERVICE,
            NOTIFICATION_IFACE,
            "ActionInvoked",
            NOTIFICATION_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_action_invoked,
            None,
        )
        self._closed_subscription = self._bus.signal_subscribe(
            NOTIFICATION_SERVICE,
            NOTIFICATION_IFACE,
            "NotificationClosed",
            NOTIFICATION_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_notification_closed,
            None,
        )
        return self._bus

    def _on_notify_finished(
        self,
        connection: Gio.DBusConnection,
        result: Gio.AsyncResult,
        user_data: tuple[Any, str, int],
    ) -> None:
        notification, tag, generation = user_data
        try:
            reply = connection.call_finish(result)
            notification_id = int(reply.unpack()[0])
        except Exception as exc:
            log.debug("WhatsApp notification was rejected: %s", exc)
            return
        if self._stopped or generation != self._generation:
            self._close_desktop_notification(notification_id)
            with suppress(Exception):
                notification.close()
            return
        self._notifications[notification_id] = notification
        self._notification_ids[id(notification)] = notification_id
        if tag:
            self._tag_ids[tag] = notification_id

    def _on_web_notification_closed(self, notification: Any) -> None:
        notification_id = self._notification_ids.pop(id(notification), 0)
        if not notification_id:
            return
        self._notifications.pop(notification_id, None)
        self._drop_tag_id(notification_id)
        self._close_desktop_notification(notification_id)

    def _close_desktop_notification(self, notification_id: int) -> None:
        if self._bus is None:
            return
        try:
            self._bus.call(
                NOTIFICATION_SERVICE,
                NOTIFICATION_PATH,
                NOTIFICATION_IFACE,
                "CloseNotification",
                GLib.Variant("(u)", (notification_id,)),
                None,
                Gio.DBusCallFlags.NONE,
                METHOD_TIMEOUT_MS,
                None,
                None,
                None,
            )
        except Exception as exc:
            log.debug("Could not close WhatsApp notification: %s", exc)

    def _on_action_invoked(
        self,
        _connection: Gio.DBusConnection,
        _sender_name: str,
        _object_path: str,
        _interface_name: str,
        _signal_name: str,
        parameters: GLib.Variant,
        _user_data: object,
    ) -> None:
        try:
            notification_id, action = parameters.unpack()
            notification = self._notifications.get(int(notification_id))
        except Exception:
            return
        if notification is None or str(action) != "default":
            return
        self._on_activate()
        try:
            notification.clicked()
        except Exception as exc:
            log.debug("Could not activate WhatsApp notification: %s", exc)

    def _on_notification_closed(
        self,
        _connection: Gio.DBusConnection,
        _sender_name: str,
        _object_path: str,
        _interface_name: str,
        _signal_name: str,
        parameters: GLib.Variant,
        _user_data: object,
    ) -> None:
        try:
            notification_id = int(parameters.unpack()[0])
        except Exception:
            return
        notification = self._notifications.pop(notification_id, None)
        if notification is not None:
            self._notification_ids.pop(id(notification), None)
        self._drop_tag_id(notification_id)
        if notification is not None:
            try:
                notification.close()
            except Exception as exc:
                log.debug("Could not close WhatsApp web notification: %s", exc)

    def _drop_tag_id(self, notification_id: int) -> None:
        for tag, existing_id in tuple(self._tag_ids.items()):
            if existing_id == notification_id:
                self._tag_ids.pop(tag, None)
