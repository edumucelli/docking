"""Tests for Search1 activation and package-aware bus ownership."""

from __future__ import annotations

from unittest.mock import MagicMock

from gi.repository import GLib

from docking.ipc.bus_host import DockBusHost
from docking.ipc.introspection import SEARCH_INTERFACE, SEARCH_OBJECT_PATH
from docking.ipc.search_service import DockSearchService
from docking.platform.app_identity import application_id, bus_name


class _Connection:
    def __init__(self, *, request_name_reply: int = 1) -> None:
        self.request_name_reply = request_name_reply
        self.calls: list[tuple[object, ...]] = []
        self.registered: list[tuple[object, ...]] = []
        self.unregistered: list[int] = []

    def call_sync(self, *args):
        self.calls.append(args)
        member = args[3]
        if member == "RequestName":
            return GLib.Variant("(u)", (self.request_name_reply,))
        return GLib.Variant("(u)", (1,))

    def register_object(self, *args):
        self.registered.append(args)
        return 42

    def unregister_object(self, registration_id: int) -> None:
        self.unregistered.append(registration_id)


def test_search_service_forwards_typed_activation_context() -> None:
    presenter = MagicMock()
    service = DockSearchService(presenter=presenter)

    assert service._dispatch(
        method_name="Show",
        parameters=GLib.Variant(
            "(sa{sv})",
            (
                "fire",
                {
                    "XDG_ACTIVATION_TOKEN": GLib.Variant("s", "token"),
                },
            ),
        ),
    )

    presenter.show.assert_called_once_with(
        initial_query="fire",
        activation_context={"XDG_ACTIVATION_TOKEN": "token"},
    )


def test_search_service_toggle_hide_and_registration() -> None:
    presenter = MagicMock()
    connection = _Connection()
    service = DockSearchService(presenter=presenter)

    service.register(connection=connection)
    assert connection.registered[0][0:2] == (
        SEARCH_OBJECT_PATH,
        service._interface_info,
    )

    assert service._dispatch(
        method_name="Toggle",
        parameters=GLib.Variant("(a{sv})", ({},)),
    )
    assert service._dispatch(
        method_name="Hide",
        parameters=GLib.Variant("()", ()),
    )
    presenter.toggle.assert_called_once_with(activation_context={})
    presenter.hide.assert_called_once_with()

    service.stop()
    assert connection.unregistered == [42]


def test_bus_host_acquires_once_registers_and_releases() -> None:
    connection = _Connection()
    interface = MagicMock()
    host = DockBusHost(name="org.example.Dock", connection=connection)

    assert host.acquire()
    assert host.acquire()
    host.register_interfaces([interface])
    host.stop()

    request_calls = [call for call in connection.calls if call[3] == "RequestName"]
    release_calls = [call for call in connection.calls if call[3] == "ReleaseName"]
    assert len(request_calls) == 1
    assert len(release_calls) == 1
    interface.register.assert_called_once_with(connection=connection)
    interface.stop.assert_called_once_with()


def test_bus_host_rejects_queued_name() -> None:
    host = DockBusHost(
        name="org.example.Dock",
        connection=_Connection(request_name_reply=3),
    )

    assert not host.acquire()


def test_package_identity() -> None:
    assert application_id(env={}) == "org.docking.Docking"
    assert application_id(env={"FLATPAK_ID": "cc.docking.Docking"}) == (
        "cc.docking.Docking"
    )
    assert bus_name(env={"FLATPAK_ID": "cc.docking.Docking"}) == ("cc.docking.Docking")


def test_search_interface_name_is_stable() -> None:
    assert SEARCH_INTERFACE == "org.docking.Docking.Search1"
