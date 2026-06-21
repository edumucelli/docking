"""Tests for the isolated D-Bus item-control service."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gi.repository import GLib

from docking.core.position import Position
from docking.ipc.introspection import ITEMS_INTERFACE, OBJECT_PATH, UNKNOWN_METHOD_ERROR
from docking.ipc.items_service import DockItemsService
from docking.ui.geometry import anchor_from_draw_rect


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


class TestDockItemsServiceEdgeCases:
    def test_start_already_started_returns_early(self, monkeypatch):
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
        monkeypatch.setattr(
            "docking.ipc.items_service.GLib.timeout_add",
            lambda *_args: 77,
        )

        service = DockItemsService(model=model, window=window)
        service.start()
        # Second start should be a no-op
        service.start()
        # Should still have only one registration
        assert len(connection.register_calls) == 1

    def test_start_bus_connection_failure_logs_warning(self, monkeypatch):
        model = _make_model()
        window = _make_window()
        monkeypatch.setattr(
            "docking.ipc.items_service.Gio.bus_get_sync",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("no bus")),
        )
        warnings: list[str] = []

        class _Capture:
            def warning(self, msg, *args):
                warnings.append(msg % args)

        monkeypatch.setattr("docking.ipc.items_service.log", _Capture())

        service = DockItemsService(model=model, window=window)
        service.start()

        assert any("Could not connect to session bus" in w for w in warnings)

    def test_start_registration_failure_unowns_and_logs(self, monkeypatch):
        model = _make_model()
        window = _make_window()
        connection = _FakeConnection()
        monkeypatch.setattr(
            "docking.ipc.items_service.Gio.bus_get_sync",
            lambda *_args: connection,
        )
        owner_id = 0

        def _bus_own_name(*_args):
            nonlocal owner_id
            owner_id = 23
            return owner_id

        monkeypatch.setattr(
            "docking.ipc.items_service.Gio.bus_own_name_on_connection",
            _bus_own_name,
        )
        # Make register_object fail
        connection.register_object = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("registration failed")
        )
        unowned: list[int] = []

        def _bus_unown_name(oid):
            unowned.append(oid)

        monkeypatch.setattr(
            "docking.ipc.items_service.Gio.bus_unown_name",
            _bus_unown_name,
        )
        warnings: list[str] = []

        class _Capture:
            def warning(self, msg, *args):
                warnings.append(msg % args)

        monkeypatch.setattr("docking.ipc.items_service.log", _Capture())

        service = DockItemsService(model=model, window=window)
        service.start()

        assert unowned == [23]
        assert any("Could not register D-Bus service" in w for w in warnings)

    def test_attach_listener_already_subscribed_is_noop(self):
        model = _make_model()
        window = _make_window()
        service = DockItemsService(model=model, window=window)
        service._subscribed_to_model = True

        service._attach_model_change_listener()

        model.add_change_listener.assert_not_called()

    def test_detach_listener_not_subscribed_is_noop(self):
        model = _make_model()
        window = _make_window()
        service = DockItemsService(model=model, window=window)
        service._subscribed_to_model = False

        service._detach_model_change_listener()

        model.remove_change_listener.assert_not_called()

    def test_schedule_changed_signal_no_connection(self):
        model = _make_model()
        window = _make_window()
        service = DockItemsService(model=model, window=window)
        service._connection = None
        service._registration_id = 1
        service._changed_source_id = 0

        # Should not raise
        service._schedule_changed_signal()
        assert service._changed_source_id == 0

    def test_emit_changed_signal_no_connection(self):
        model = _make_model()
        window = _make_window()
        service = DockItemsService(model=model, window=window)
        service._connection = None
        service._registration_id = 1
        service._changed_source_id = 5

        result = service._emit_changed_signal()

        assert result is False

    def test_emit_changed_signal_exception_logs_warning(self, monkeypatch):
        model = _make_model()
        window = _make_window()
        connection = _FakeConnection()
        connection.emit_signal = lambda *args: (_ for _ in ()).throw(
            RuntimeError("emit failed")
        )
        service = DockItemsService(model=model, window=window)
        service._connection = connection
        service._registration_id = 1
        warnings: list[str] = []

        class _Capture:
            def warning(self, msg, *args):
                warnings.append(msg % args)

        monkeypatch.setattr("docking.ipc.items_service.log", _Capture())

        result = service._emit_changed_signal()

        assert result is False
        assert any("Failed to emit D-Bus Changed signal" in w for w in warnings)

    def test_handle_method_call_unknown_interface(self):
        service = DockItemsService(model=_make_model(), window=_make_window())
        invocation = _FakeInvocation()

        service._handle_method_call(
            None,
            "",
            OBJECT_PATH,
            "org.unknown.Interface",
            "SomeMethod",
            GLib.Variant("()", ()),
            invocation,
        )

        assert invocation.error == (
            UNKNOWN_METHOD_ERROR,
            "Unknown interface: org.unknown.Interface",
        )

    def test_handle_method_call_dispatch_exception(self, monkeypatch):
        service = DockItemsService(model=_make_model(), window=_make_window())
        invocation = _FakeInvocation()
        # Force an exception in dispatch
        monkeypatch.setattr(
            service,
            "_dispatch_call",
            lambda **kwargs: (_ for _ in ()).throw(ValueError("boom")),
        )

        service._handle_method_call(
            None,
            "",
            OBJECT_PATH,
            ITEMS_INTERFACE,
            "SomeMethod",
            GLib.Variant("()", ()),
            invocation,
        )

        assert invocation.error == (
            "org.docking.Docking.Error.Failed",
            "boom",
        )

    def test_handle_method_call_returns_value_on_success(self):
        service = DockItemsService(model=_make_model(), window=_make_window())
        invocation = _FakeInvocation()

        service._handle_method_call(
            None,
            "",
            OBJECT_PATH,
            ITEMS_INTERFACE,
            "GetCount",
            GLib.Variant("()", ()),
            invocation,
        )

        assert invocation.error is None
        assert invocation.value is not None

    def test_dispatch_call_wrong_argument_count_raises_valueerror(self):
        service = DockItemsService(model=_make_model(), window=_make_window())

        # Pin expects (s) but we pass (ii)
        with pytest.raises(ValueError, match="expects one string argument"):
            service._dispatch_call(
                method_name="Pin",
                parameters=GLib.Variant("(ii)", (1, 2)),
            )

    def test_dispatch_call_non_string_argument_raises_valueerror(self):
        service = DockItemsService(model=_make_model(), window=_make_window())

        with pytest.raises(ValueError, match="expects one string argument"):
            service._dispatch_call(
                method_name="Pin",
                parameters=GLib.Variant("(i)", (42,)),
            )

    def test_get_hover_anchor_none_returns_failure_tuple(self):
        model = _make_model()
        window = _make_window()
        window.get_hover_anchor = MagicMock(return_value=None)
        service = DockItemsService(model=model, window=window)

        result = service._dispatch_call(
            method_name="GetHoverAnchor",
            parameters=GLib.Variant("(s)", ("missing.desktop",)),
        )

        assert result.unpack() == (False, -1, -1, "")

    def test_pin_item_already_pinned_returns_false(self):
        model = _make_model()
        service = DockItemsService(model=model, window=_make_window())

        result = service._dispatch_call(
            method_name="Pin",
            parameters=GLib.Variant("(s)", ("firefox.desktop",)),
        )

        assert result.unpack() == (False,)
        model.pin_item.assert_not_called()

    def test_unpin_transient_item_returns_false(self):
        model = _make_model()
        service = DockItemsService(model=model, window=_make_window())

        result = service._dispatch_call(
            method_name="Unpin",
            parameters=GLib.Variant("(s)", ("missing.desktop",)),
        )

        assert result.unpack() == (False,)
        model.unpin_item.assert_not_called()

    def test_remove_is_alias_for_unpin(self):
        model = _make_model()
        service = DockItemsService(model=model, window=_make_window())

        # Remove on pinned item succeeds (delegates to unpin)
        result = service._dispatch_call(
            method_name="Remove",
            parameters=GLib.Variant("(s)", ("firefox.desktop",)),
        )

        assert result.unpack() == (True,)
        model.unpin_item.assert_called_once_with("firefox.desktop")


class TestHoverAnchorHelper:
    def test_hover_anchor_matches_edge_rules(self):
        rect = SimpleNamespace(x=10, y=20, w=30, h=40)

        assert anchor_from_draw_rect(
            win_x=100,
            win_y=200,
            draw_rect=rect,
            position=Position.BOTTOM,
        ) == (110, 220)
        assert anchor_from_draw_rect(
            win_x=100,
            win_y=200,
            draw_rect=rect,
            position=Position.TOP,
        ) == (110, 260)
        assert anchor_from_draw_rect(
            win_x=100,
            win_y=200,
            draw_rect=rect,
            position=Position.LEFT,
        ) == (140, 220)
        assert anchor_from_draw_rect(
            win_x=100,
            win_y=200,
            draw_rect=rect,
            position=Position.RIGHT,
        ) == (110, 220)
