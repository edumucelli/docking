"""Tests for the isolated D-Bus item-control service."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gi.repository import GLib

from docking.core.position import Position
from docking.ipc.introspection import ITEMS_INTERFACE, OBJECT_PATH
from docking.ipc.items_service import DockItemsService
from docking.ui.dock_window import hover_anchor_from_draw_rect


class _FakeConnection:
    def __init__(self) -> None:
        self.register_calls: list[tuple[object, ...]] = []
        self.unregistered: list[int] = []
        self.emitted: list[tuple[object, ...]] = []

    def register_object(
        self,
        object_path,
        interface_info,
        method_call_closure,
        get_property_closure,
        set_property_closure,
    ):
        self.register_calls.append(
            (
                object_path,
                interface_info.name,
                method_call_closure,
                get_property_closure,
                set_property_closure,
            )
        )
        return 17

    def unregister_object(self, registration_id: int) -> None:
        self.unregistered.append(registration_id)

    def emit_signal(
        self, destination, object_path, interface_name, signal_name, parameters
    ) -> None:
        self.emitted.append(
            (destination, object_path, interface_name, signal_name, parameters)
        )


class _FakeInvocation:
    def __init__(self) -> None:
        self.value = None
        self.error = None

    def return_value(self, value) -> None:
        self.value = value

    def return_dbus_error(self, name: str, message: str) -> None:
        self.error = (name, message)


def _make_item(desktop_id: str, *, is_pinned: bool) -> SimpleNamespace:
    return SimpleNamespace(desktop_id=desktop_id, is_pinned=is_pinned)


def _make_model() -> SimpleNamespace:
    pinned = [_make_item("firefox.desktop", is_pinned=True)]
    transient = [_make_item("slack.desktop", is_pinned=False)]
    lookup = {item.desktop_id: item for item in pinned + transient}
    listeners: list[object] = []
    model = SimpleNamespace()
    model.pinned_items = list(pinned)
    model.visible_items = MagicMock(return_value=list(pinned + transient))
    model.find_by_desktop_id = MagicMock(
        side_effect=lambda desktop_id: lookup.get(desktop_id)
    )
    model.pin_item = MagicMock()
    model.unpin_item = MagicMock()
    model._listeners = listeners
    model.add_change_listener = MagicMock(side_effect=listeners.append)
    model.remove_change_listener = MagicMock(
        side_effect=lambda callback: listeners.remove(callback)
    )
    return model


def _make_window() -> SimpleNamespace:
    return SimpleNamespace(
        get_hover_anchor=MagicMock(return_value=(101, 202, "bottom"))
    )


class TestDockItemsServiceLifecycle:
    def test_start_registers_object_and_wraps_model_callback(self, monkeypatch):
        model = _make_model()
        window = _make_window()
        connection = _FakeConnection()

        monkeypatch.setattr(
            "docking.ipc.items_service.Gio.bus_get_sync",
            lambda *_args: connection,
        )
        monkeypatch.setattr(
            "docking.ipc.items_service.Gio.bus_own_name_on_connection",
            lambda *_args: 23,
        )

        timeouts: list[tuple[int, object]] = []

        def _timeout_add(delay, callback):
            timeouts.append((delay, callback))
            return 99

        monkeypatch.setattr("docking.ipc.items_service.GLib.timeout_add", _timeout_add)

        service = DockItemsService(model=model, window=window)
        service.start()

        assert connection.register_calls == [
            (OBJECT_PATH, ITEMS_INTERFACE, service._handle_method_call, None, None)
        ]
        model.add_change_listener.assert_called_once_with(service._handle_model_change)
        assert model._listeners == [service._handle_model_change]

        model._listeners[0]()

        assert timeouts and timeouts[0][0] == 500

    def test_stop_unregisters_and_detaches_model_listener(self, monkeypatch):
        model = _make_model()
        window = _make_window()
        connection = _FakeConnection()
        unowned: list[int] = []
        removed: list[int] = []

        monkeypatch.setattr(
            "docking.ipc.items_service.Gio.bus_get_sync",
            lambda *_args: connection,
        )
        monkeypatch.setattr(
            "docking.ipc.items_service.Gio.bus_own_name_on_connection",
            lambda *_args: 23,
        )
        monkeypatch.setattr(
            "docking.ipc.items_service.Gio.bus_unown_name",
            lambda owner_id: unowned.append(owner_id),
        )
        monkeypatch.setattr(
            "docking.ipc.items_service.GLib.timeout_add",
            lambda *_args: 77,
        )
        monkeypatch.setattr(
            "docking.ipc.items_service.GLib.source_remove",
            lambda source_id: removed.append(source_id),
        )

        service = DockItemsService(model=model, window=window)
        service.start()
        model._listeners[0]()

        service.stop()

        assert connection.unregistered == [17]
        assert unowned == [23]
        assert removed == [77]
        model.remove_change_listener.assert_called_once_with(
            service._handle_model_change
        )
        assert model._listeners == []


class TestDockItemsServiceDispatch:
    def test_typed_list_and_count_methods(self):
        service = DockItemsService(model=_make_model(), window=_make_window())

        count = service._dispatch_call(
            method_name="GetCount",
            parameters=GLib.Variant("()", ()),
        )
        pinned = service._dispatch_call(
            method_name="ListPinnedIds",
            parameters=GLib.Variant("()", ()),
        )
        transient = service._dispatch_call(
            method_name="ListTransientIds",
            parameters=GLib.Variant("()", ()),
        )

        assert count.unpack() == (2,)
        assert pinned.unpack() == (["firefox.desktop"],)
        assert transient.unpack() == (["slack.desktop"],)

    def test_pin_and_unpin_require_expected_item_state(self):
        model = _make_model()
        service = DockItemsService(model=model, window=_make_window())

        result = service._dispatch_call(
            method_name="Pin",
            parameters=GLib.Variant("(s)", ("slack.desktop",)),
        )
        assert result.unpack() == (True,)
        model.pin_item.assert_called_once_with("slack.desktop")

        result = service._dispatch_call(
            method_name="Unpin",
            parameters=GLib.Variant("(s)", ("firefox.desktop",)),
        )
        assert result.unpack() == (True,)
        model.unpin_item.assert_called_once_with("firefox.desktop")

    def test_remove_rejects_transient_items(self):
        model = _make_model()
        service = DockItemsService(model=model, window=_make_window())

        result = service._dispatch_call(
            method_name="Remove",
            parameters=GLib.Variant("(s)", ("slack.desktop",)),
        )

        assert result.unpack() == (False,)
        model.unpin_item.assert_not_called()

    def test_get_hover_anchor_returns_typed_tuple(self):
        service = DockItemsService(model=_make_model(), window=_make_window())

        result = service._dispatch_call(
            method_name="GetHoverAnchor",
            parameters=GLib.Variant("(s)", ("firefox.desktop",)),
        )

        assert result.unpack() == (True, 101, 202, "bottom")

    def test_unknown_method_returns_dbus_error(self):
        service = DockItemsService(model=_make_model(), window=_make_window())
        invocation = _FakeInvocation()

        service._handle_method_call(
            None,
            "",
            OBJECT_PATH,
            ITEMS_INTERFACE,
            "Nope",
            GLib.Variant("()", ()),
            invocation,
        )

        assert invocation.error == (
            "org.freedesktop.DBus.Error.UnknownMethod",
            "Unknown method: Nope",
        )


class TestDockItemsServiceSignals:
    def test_changed_signal_is_debounced(self, monkeypatch):
        model = _make_model()
        window = _make_window()
        connection = _FakeConnection()

        monkeypatch.setattr(
            "docking.ipc.items_service.Gio.bus_get_sync",
            lambda *_args: connection,
        )
        monkeypatch.setattr(
            "docking.ipc.items_service.Gio.bus_own_name_on_connection",
            lambda *_args: 23,
        )

        scheduled: list[object] = []

        def _timeout_add(_delay, callback):
            scheduled.append(callback)
            return len(scheduled)

        monkeypatch.setattr("docking.ipc.items_service.GLib.timeout_add", _timeout_add)

        service = DockItemsService(model=model, window=window)
        service.start()

        service._schedule_changed_signal()
        service._schedule_changed_signal()

        assert len(scheduled) == 1
        scheduled[0]()
        assert connection.emitted == [
            (None, OBJECT_PATH, ITEMS_INTERFACE, "Changed", None)
        ]


class TestHoverAnchorHelper:
    def test_hover_anchor_matches_edge_rules(self):
        rect = SimpleNamespace(x=10, y=20, w=30, h=40)

        assert hover_anchor_from_draw_rect(
            win_x=100,
            win_y=200,
            draw_rect=rect,
            position=Position.BOTTOM,
        ) == (110, 220)
        assert hover_anchor_from_draw_rect(
            win_x=100,
            win_y=200,
            draw_rect=rect,
            position=Position.TOP,
        ) == (110, 260)
        assert hover_anchor_from_draw_rect(
            win_x=100,
            win_y=200,
            draw_rect=rect,
            position=Position.LEFT,
        ) == (140, 220)
        assert hover_anchor_from_draw_rect(
            win_x=100,
            win_y=200,
            draw_rect=rect,
            position=Position.RIGHT,
        ) == (110, 220)
