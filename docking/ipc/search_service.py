# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Narrow D-Bus activation surface for Docking Search."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from docking.ipc.introspection import (
    SEARCH_INTERFACE,
    SEARCH_INTROSPECTION_XML,
    SEARCH_OBJECT_PATH,
    UNKNOWN_METHOD_ERROR,
)
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.search.presenter import SearchPresenter

log = get_logger("ipc.search")


def _unpack_context(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("activation_context must be a dictionary")
    result: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        result[key] = value.unpack() if isinstance(value, GLib.Variant) else value
    return result


class DockSearchService:
    """Register Search1 and forward activation to the UI-owned presenter."""

    def __init__(self, *, presenter: SearchPresenter) -> None:
        self._presenter = presenter
        self._connection: Gio.DBusConnection | None = None
        self._registration_id = 0
        self._interface_info = Gio.DBusNodeInfo.new_for_xml(
            SEARCH_INTROSPECTION_XML
        ).interfaces[0]

    def register(self, *, connection: Gio.DBusConnection) -> None:
        if self._registration_id > 0:
            return
        self._registration_id = connection.register_object(
            SEARCH_OBJECT_PATH,
            self._interface_info,
            self._handle_method_call,
            None,
            None,
        )
        self._connection = connection

    def stop(self) -> None:
        if self._connection is not None and self._registration_id > 0:
            self._connection.unregister_object(self._registration_id)
        self._registration_id = 0
        self._connection = None

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
        if interface_name != SEARCH_INTERFACE:
            invocation.return_dbus_error(
                UNKNOWN_METHOD_ERROR,
                f"Unknown interface: {interface_name}",
            )
            return
        try:
            handled = self._dispatch(method_name=method_name, parameters=parameters)
        except Exception as exc:
            log.exception("Unhandled exception in D-Bus method %s", method_name)
            invocation.return_dbus_error(
                "org.docking.Docking.Error.Failed",
                str(exc),
            )
            return
        if not handled:
            invocation.return_dbus_error(
                UNKNOWN_METHOD_ERROR,
                f"Unknown method: {method_name}",
            )
            return
        invocation.return_value(GLib.Variant("()", ()))

    def _dispatch(self, *, method_name: str, parameters: GLib.Variant) -> bool:
        args = parameters.unpack()
        if method_name == "Hide":
            if args:
                raise ValueError("Hide expects no arguments")
            self._presenter.hide()
            return True
        if method_name == "Show":
            if len(args) != 2 or not isinstance(args[0], str):
                raise ValueError("Show expects a query and activation context")
            self._presenter.show(
                initial_query=args[0],
                activation_context=_unpack_context(args[1]),
            )
            return True
        if method_name == "Toggle":
            if len(args) != 1:
                raise ValueError("Toggle expects an activation context")
            self._presenter.toggle(activation_context=_unpack_context(args[0]))
            return True
        return False


__all__ = ["DockSearchService"]
