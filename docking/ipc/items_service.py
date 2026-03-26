"""Session-bus adapter for Docking item control.

Why this module exists

Docking's model and UI already own the real behavior:

- the model owns pinned/transient item state,
- the window/geometry layer owns hover anchor placement,
- the application bootstrap owns lifetime.

This module does not introduce new dock behavior. It only exposes a stable,
versioned D-Bus surface that forwards to those existing owners.

Isolation rules

- only this package should know bus names, object paths, and Gio D-Bus details
- the model stays transport-agnostic
- the UI stays transport-agnostic
- startup wiring happens at the application edge
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from docking.ipc.introspection import (
    BUS_NAME,
    ITEMS_INTERFACE,
    ITEMS_INTROSPECTION_XML,
    OBJECT_PATH,
    UNKNOWN_METHOD_ERROR,
)
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.platform.model import DockModel
    from docking.ui.dock_window import DockWindow

_log = get_logger(name="ipc.items")
_CHANGED_DEBOUNCE_MS = 500


class DockItemsService:
    """Expose a narrow item-control API on the session bus."""

    def __init__(self, *, model: DockModel, window: DockWindow) -> None:
        self._model = model
        self._window = window
        self._connection: Gio.DBusConnection | None = None
        self._owner_id: int = 0
        self._registration_id: int = 0
        self._changed_source_id: int = 0
        self._previous_on_change: Callable[[], None] | None = None
        self._model_change_hook: Callable[[], None] = self._handle_model_change
        self._interface_info = Gio.DBusNodeInfo.new_for_xml(
            ITEMS_INTROSPECTION_XML
        ).interfaces[0]

    def start(self) -> None:
        """Register the service on the session bus.

        Failure to acquire a session bus should not stop the dock from running.
        """
        if self._registration_id > 0:
            return

        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as exc:
            _log.warning("Could not connect to session bus for D-Bus service: %s", exc)
            return

        owner_id = 0
        try:
            owner_id = Gio.bus_own_name_on_connection(
                connection,
                BUS_NAME,
                Gio.BusNameOwnerFlags.NONE,
                None,
                None,
            )
            registration_id = connection.register_object(
                OBJECT_PATH,
                self._interface_info,
                self._handle_method_call,
                None,
                None,
            )
        except Exception as exc:
            _log.warning("Could not register D-Bus service: %s", exc)
            if owner_id > 0:
                Gio.bus_unown_name(owner_id)
            return

        self._connection = connection
        self._owner_id = owner_id
        self._registration_id = registration_id
        self._attach_model_change_hook()

    def stop(self) -> None:
        """Detach bus resources and restore the previous model callback."""
        if self._changed_source_id > 0:
            GLib.source_remove(self._changed_source_id)
            self._changed_source_id = 0

        if self._connection is not None and self._registration_id > 0:
            self._connection.unregister_object(self._registration_id)
        self._registration_id = 0

        if self._owner_id > 0:
            Gio.bus_unown_name(self._owner_id)
        self._owner_id = 0
        self._connection = None

        if self._model.on_change is self._model_change_hook:
            self._model.on_change = self._previous_on_change
        self._previous_on_change = None

    def _attach_model_change_hook(self) -> None:
        self._previous_on_change = self._model.on_change
        self._model.on_change = self._model_change_hook

    def _handle_model_change(self) -> None:
        if self._previous_on_change:
            self._previous_on_change()
        self._schedule_changed_signal()

    def _schedule_changed_signal(self) -> None:
        if self._connection is None or self._registration_id <= 0:
            return
        if self._changed_source_id > 0:
            return
        self._changed_source_id = GLib.timeout_add(
            _CHANGED_DEBOUNCE_MS, self._emit_changed_signal
        )

    def _emit_changed_signal(self) -> bool:
        self._changed_source_id = 0
        if self._connection is None or self._registration_id <= 0:
            return False
        try:
            self._connection.emit_signal(
                None,
                OBJECT_PATH,
                ITEMS_INTERFACE,
                "Changed",
                None,
            )
        except Exception as exc:
            _log.warning("Failed to emit D-Bus Changed signal: %s", exc)
        return False

    def _handle_method_call(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        if interface_name != ITEMS_INTERFACE:
            invocation.return_dbus_error(
                UNKNOWN_METHOD_ERROR,
                f"Unknown interface: {interface_name}",
            )
            return

        try:
            result = self._dispatch_call(method_name=method_name, parameters=parameters)
        except Exception as exc:
            _log.exception("Unhandled exception in D-Bus method %s", method_name)
            invocation.return_dbus_error(
                "org.docking.Docking.Error.Failed",
                str(exc),
            )
            return

        if result is None:
            invocation.return_dbus_error(
                UNKNOWN_METHOD_ERROR,
                f"Unknown method: {method_name}",
            )
            return

        invocation.return_value(result)

    def _dispatch_call(
        self, *, method_name: str, parameters: GLib.Variant
    ) -> GLib.Variant | None:
        if method_name == "GetCount":
            return GLib.Variant("(i)", (len(self._model.visible_items()),))

        if method_name == "ListPinnedIds":
            ids = [item.desktop_id for item in self._model.pinned_items]
            return GLib.Variant("(as)", (ids,))

        if method_name == "ListTransientIds":
            ids = [
                item.desktop_id
                for item in self._model.visible_items()
                if not item.is_pinned
            ]
            return GLib.Variant("(as)", (ids,))

        if method_name not in {"Pin", "Unpin", "Remove", "GetHoverAnchor"}:
            return None

        args = parameters.unpack()
        if len(args) != 1 or not isinstance(args[0], str):
            raise ValueError(f"{method_name} expects one string argument")
        desktop_id = args[0]

        if method_name == "Pin":
            return GLib.Variant("(b)", (self._pin(desktop_id=desktop_id),))
        if method_name == "Unpin":
            return GLib.Variant("(b)", (self._unpin(desktop_id=desktop_id),))
        if method_name == "Remove":
            return GLib.Variant("(b)", (self._remove(desktop_id=desktop_id),))

        anchor = self._window.get_hover_anchor(desktop_id=desktop_id)
        if anchor is None:
            return GLib.Variant("(biis)", (False, -1, -1, ""))
        x, y, position = anchor
        return GLib.Variant("(biis)", (True, x, y, position))

    def _pin(self, *, desktop_id: str) -> bool:
        item = self._model.find_by_desktop_id(desktop_id=desktop_id)
        if item is None or item.is_pinned:
            return False
        self._model.pin_item(desktop_id)
        return True

    def _unpin(self, *, desktop_id: str) -> bool:
        item = self._model.find_by_desktop_id(desktop_id=desktop_id)
        if item is None or not item.is_pinned:
            return False
        self._model.unpin_item(desktop_id)
        return True

    def _remove(self, *, desktop_id: str) -> bool:
        return self._unpin(desktop_id=desktop_id)
