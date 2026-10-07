from __future__ import annotations

from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.applications.types import ApplicationMatch
from docking.platform.backends.kwin import atspi_window
from tests.platform.application_fakes import identity_services


@pytest.fixture(autouse=True)
def isolate_accessibility_override(monkeypatch):
    monkeypatch.delenv("AT_SPI_BUS_ADDRESS", raising=False)


def _service() -> tuple[atspi_window.AtspiWindowService, MagicMock]:
    model = MagicMock()
    model.visible_items.return_value = []
    return (
        atspi_window.AtspiWindowService(
            model=model,
            **identity_services(),
        ),
        model,
    )


def test_accessibility_address_is_discovered_on_current_session_bus(monkeypatch):
    session = MagicMock()
    session.call_sync.return_value = atspi_window.GLib.Variant(
        "(s)", ("unix:path=/tmp/session-b/accessibility",)
    )
    get_bus = MagicMock(return_value=session)
    monkeypatch.setattr(atspi_window.Gio, "bus_get_sync", get_bus)

    assert atspi_window._at_spi_address() == "unix:path=/tmp/session-b/accessibility"

    get_bus.assert_called_once_with(atspi_window.Gio.BusType.SESSION, None)
    args = session.call_sync.call_args.args
    assert args[:5] == (
        "org.a11y.Bus",
        "/org/a11y/bus",
        "org.a11y.Bus",
        "GetAddress",
        None,
    )
    assert args[5].dup_string() == "(s)"
    assert 0 < args[7] <= 2000
    session.close_sync.assert_not_called()


@pytest.mark.parametrize("address", ["", "not a bus address"])
def test_invalid_discovered_address_has_no_socket_fallback(monkeypatch, address):
    session = MagicMock()
    session.call_sync.return_value = atspi_window.GLib.Variant("(s)", (address,))
    monkeypatch.setattr(atspi_window.Gio, "bus_get_sync", lambda *_: session)

    with pytest.raises(ValueError):
        atspi_window._at_spi_address()


def test_missing_discovery_service_has_no_socket_fallback(monkeypatch):
    session = MagicMock()
    session.call_sync.side_effect = RuntimeError("No org.a11y.Bus in this session")
    monkeypatch.setattr(atspi_window.Gio, "bus_get_sync", lambda *_: session)

    with pytest.raises(RuntimeError, match=r"No org\.a11y\.Bus"):
        atspi_window._at_spi_address()


def test_explicit_address_works_without_session_discovery(monkeypatch):
    monkeypatch.setenv("AT_SPI_BUS_ADDRESS", "unix:path=/tmp/explicit-accessibility")
    get_bus = MagicMock(side_effect=RuntimeError("No session bus"))
    monkeypatch.setattr(atspi_window.Gio, "bus_get_sync", get_bus)

    assert atspi_window._at_spi_address() == "unix:path=/tmp/explicit-accessibility"
    get_bus.assert_not_called()


def test_invalid_explicit_address_does_not_fall_back_to_another_bus(monkeypatch):
    monkeypatch.setenv("AT_SPI_BUS_ADDRESS", "not an address")
    get_bus = MagicMock()
    monkeypatch.setattr(atspi_window.Gio, "bus_get_sync", get_bus)

    with pytest.raises(ValueError, match="AT_SPI_BUS_ADDRESS"):
        atspi_window._at_spi_address()
    get_bus.assert_not_called()


def test_kwin_fallback_is_represented_as_application_match() -> None:
    service, model = _service()
    window = atspi_window._AtspiWindow("window-1")
    window.app_name = "Unregistered"
    window.pid = 73
    service._windows[window.window_id.value] = window

    keep_source = service._publish_running()

    assert not keep_source
    assert isinstance(window.application_match, ApplicationMatch)
    assert window.application_match.desktop_id == "kwin:Unregistered"
    assert window.application_match.application is None
    running = model.update_running.call_args.kwargs["running"]
    assert tuple(running) == ("kwin:Unregistered",)


def test_kwin_payload_flows_through_real_matcher_to_model() -> None:
    service, model = _service()
    window = atspi_window._AtspiWindow("window-1")
    window.app_name = "Alacritty"
    window.pid = 73
    service._windows[window.window_id.value] = window

    keep_source = service._publish_running()

    assert not keep_source
    assert window.application_match is not None
    assert window.application_match.desktop_id == "Alacritty.desktop"
    assert window.application_match.application is not None
    running = model.update_running.call_args.kwargs["running"]
    assert tuple(running) == ("Alacritty.desktop",)


def test_kwin_start_and_stop_are_idempotent(monkeypatch) -> None:
    service, _model = _service()
    connection = SimpleNamespace(
        call_sync=MagicMock(),
        close_sync=MagicMock(),
    )
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(
        atspi_window.Gio.DBusConnection,
        "new_for_address_sync",
        connect,
    )
    schedule_refresh = MagicMock()
    monkeypatch.setattr(service, "_schedule_refresh", schedule_refresh)
    timeout_add = MagicMock(return_value=17)
    source_remove = MagicMock()
    monkeypatch.setattr(atspi_window.GLib, "timeout_add", timeout_add)
    monkeypatch.setattr(atspi_window.GLib, "source_remove", source_remove)

    service.start()
    service.start()
    # The worker owns connection setup; start itself must not block on D-Bus.
    connect.assert_not_called()
    service._connection = connection
    service.stop()
    service.stop()

    schedule_refresh.assert_called_once_with()
    timeout_add.assert_called_once()
    source_remove.assert_called_once_with(17)
    connection.close_sync.assert_called_once_with(None)


def test_kwin_background_refresh_marshals_publication_to_glib(monkeypatch) -> None:
    service, model = _service()
    result = SimpleNamespace(
        get_child_value=lambda _index: SimpleNamespace(
            unpack=lambda: [":1.2"],
        )
    )
    connection = SimpleNamespace(
        call_sync=MagicMock(return_value=result),
        is_closed=lambda: False,
        get_unique_name=lambda: ":1.99",
    )
    service._connection = connection
    service._running = True
    service._lifecycle_token = 1
    service._refresh_token = 1

    def enumerate_service(_connection, _service_name, windows) -> None:
        window = atspi_window._AtspiWindow("window-2")
        window.app_name = "Application"
        windows[window.window_id.value] = window

    monkeypatch.setattr(service, "_enumerate_service", enumerate_service)
    idle_add = MagicMock(return_value=1)
    monkeypatch.setattr(atspi_window.GLib, "idle_add", idle_add)

    service._refresh(1, connection)

    idle_add.assert_called_once_with(service._publish_running, 1)
    model.update_running.assert_not_called()


def test_kwin_blocked_refresh_cannot_repopulate_or_publish_after_stop(
    monkeypatch,
) -> None:
    service, model = _service()
    result = SimpleNamespace(
        get_child_value=lambda _index: SimpleNamespace(
            unpack=lambda: [":1.2"],
        )
    )
    connection = SimpleNamespace(
        call_sync=MagicMock(return_value=result),
        close_sync=MagicMock(),
        is_closed=lambda: False,
        get_unique_name=lambda: ":1.99",
    )
    service._connection = connection
    service._running = True
    service._lifecycle_token = 1
    entered = Event()
    release = Event()

    def enumerate_service(_connection, _service_name, windows) -> None:
        entered.set()
        assert release.wait(timeout=2)
        window = atspi_window._AtspiWindow("late-window")
        window.app_name = "Late Application"
        windows[window.window_id.value] = window

    workers: list[Thread] = []

    def thread_factory(*args, **kwargs) -> Thread:
        worker = Thread(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(service, "_enumerate_service", enumerate_service)
    monkeypatch.setattr(atspi_window, "Thread", thread_factory)
    idle_add = MagicMock(return_value=1)
    monkeypatch.setattr(atspi_window.GLib, "idle_add", idle_add)

    service._schedule_refresh()
    assert entered.wait(timeout=2)
    service.stop()
    release.set()
    workers[0].join(timeout=2)

    assert not workers[0].is_alive()
    assert service.list_all_windows() == ()
    assert service._on_refresh_timer() is False
    idle_add.assert_not_called()
    model.update_running.assert_not_called()


def _connection():
    return SimpleNamespace(
        call_sync=MagicMock(return_value=atspi_window.GLib.Variant("(as)", ([],))),
        is_closed=MagicMock(return_value=False),
        get_unique_name=lambda: ":1.99",
        close_sync=MagicMock(),
        set_exit_on_close=MagicMock(),
    )


def test_registry_application_is_walked_not_reported_as_a_window(monkeypatch):
    service, _ = _service()
    connection = _connection()
    connection.call_sync.return_value = atspi_window.GLib.Variant(
        "(a(so))", ([(":1.0", "/application")],)
    )
    monkeypatch.setattr(service, "_get_prop", lambda *_: "Registry")
    monkeypatch.setattr(service, "_get_role_name", lambda *_: "application")
    collect, walk = MagicMock(), MagicMock()
    monkeypatch.setattr(service, "_collect_window", collect)
    monkeypatch.setattr(service, "_walk_children", walk)
    windows = {}

    service._enumerate_service(connection, ":1.5", windows)

    collect.assert_not_called()
    walk.assert_called_once_with(
        connection, ":1.0", "/application", "Registry", windows, depth=1
    )


@pytest.fixture
def inline_worker(monkeypatch):
    """Deterministic lifecycle tests; separate blocking tests use real threads."""
    monkeypatch.setattr(
        atspi_window,
        "Thread",
        lambda *, target, args, daemon: SimpleNamespace(start=lambda: target(*args)),
    )
    monkeypatch.setattr(atspi_window.GLib, "timeout_add", MagicMock(return_value=17))
    monkeypatch.setattr(atspi_window.GLib, "source_remove", MagicMock())
    monkeypatch.setattr(atspi_window.GLib, "idle_add", MagicMock(return_value=1))


@pytest.mark.usefixtures("inline_worker")
def test_connection_authenticates_as_bus_and_does_not_send_second_hello(monkeypatch):
    service, _ = _service()
    connection = _connection()
    discover = MagicMock(return_value="unix:path=/tmp/current-atspi")
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(atspi_window, "_at_spi_address", discover)
    monkeypatch.setattr(
        atspi_window.Gio.DBusConnection, "new_for_address_sync", connect
    )
    service.start()
    try:
        args = connect.call_args.args
        assert args[0] == "unix:path=/tmp/current-atspi"
        assert args[1] == (
            atspi_window.Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | atspi_window.Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        )
        assert args[3] is service._connect_cancellable
        discover.assert_called_once_with(service._connect_cancellable)
        connection.set_exit_on_close.assert_called_once_with(False)
        assert all(
            call.args[3] != "Hello" for call in connection.call_sync.call_args_list
        )
    finally:
        service.stop()


@pytest.mark.usefixtures("inline_worker")
def test_missing_bus_retries_without_another_start(monkeypatch):
    service, _ = _service()
    connection = _connection()
    discover = MagicMock(side_effect=[RuntimeError("missing"), "unix:path=/tmp/late"])
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(atspi_window, "_at_spi_address", discover)
    monkeypatch.setattr(
        atspi_window.Gio.DBusConnection, "new_for_address_sync", connect
    )
    service.start()
    try:
        assert service._running and service._connection is None
        connect.assert_not_called()
        assert service._on_refresh_timer() is True
        assert service._connection is connection
        assert discover.call_count == 2
    finally:
        service.stop()


@pytest.mark.usefixtures("inline_worker")
def test_closed_bus_clears_windows_and_rediscovers_address(monkeypatch):
    service, model = _service()
    old, new = _connection(), _connection()
    discover = MagicMock(side_effect=["unix:path=/tmp/old", "unix:path=/tmp/new"])
    connect = MagicMock(side_effect=[old, new])
    monkeypatch.setattr(atspi_window, "_at_spi_address", discover)
    monkeypatch.setattr(
        atspi_window.Gio.DBusConnection, "new_for_address_sync", connect
    )
    service.start()
    try:
        service._windows["old"] = atspi_window._AtspiWindow("old")
        old.is_closed.return_value = True
        service._on_refresh_timer()
        assert [call.args[0] for call in connect.call_args_list] == [
            "unix:path=/tmp/old",
            "unix:path=/tmp/new",
        ]
        assert service._connection is new
        assert service.list_all_windows() == ()
        model.update_running.assert_not_called()
    finally:
        service.stop()


@pytest.mark.usefixtures("inline_worker")
def test_old_connection_cannot_replace_restarted_service(monkeypatch):
    service, _ = _service()
    old, new = _connection(), _connection()
    first = True

    def discover(_cancellable):
        nonlocal first
        if first:
            first = False
            service.stop()
            service.start()
            return "unix:path=/tmp/old"
        return "unix:path=/tmp/new"

    monkeypatch.setattr(atspi_window, "_at_spi_address", discover)
    monkeypatch.setattr(
        atspi_window.Gio.DBusConnection,
        "new_for_address_sync",
        lambda address, *_: old if address.endswith("old") else new,
    )
    monkeypatch.setattr(
        atspi_window.GLib, "timeout_add", MagicMock(side_effect=[17, 18])
    )
    service.start()
    try:
        assert service._connection is new
        assert service._refresh_source_id == 17
        assert service._refresh_token is None
        old.close_sync.assert_called_once_with(None)
        new.close_sync.assert_not_called()
        assert service._publish_running(1) is False
    finally:
        service.stop()


@pytest.mark.usefixtures("inline_worker")
def test_first_unique_name_is_not_assumed_to_be_a_system_service(
    monkeypatch,
):
    service, _ = _service()
    connection = _connection()
    connection.call_sync.return_value = atspi_window.GLib.Variant(
        "(as)", ([":1.0", ":1.99", "org.a11y.atspi.Registry"],)
    )
    monkeypatch.setattr(atspi_window, "_at_spi_address", lambda _: "unix:path=/tmp/bus")
    monkeypatch.setattr(
        atspi_window.Gio.DBusConnection, "new_for_address_sync", lambda *_: connection
    )
    enumerate_service = MagicMock()
    monkeypatch.setattr(service, "_enumerate_service", enumerate_service)
    service.start()
    try:
        assert [call.args[1] for call in enumerate_service.call_args_list] == [":1.0"]
    finally:
        service.stop()


def test_stop_during_discovery_cancels_and_discards_late_connection(monkeypatch):
    service, model = _service()
    entered, release = Event(), Event()
    connection = _connection()
    cancellations = []
    workers = []

    def discover(cancellable):
        cancellations.append(cancellable)
        entered.set()
        assert release.wait(2)
        return "unix:path=/tmp/late"

    def worker(**kwargs):
        thread = Thread(**kwargs)
        workers.append(thread)
        return thread

    monkeypatch.setattr(atspi_window, "_at_spi_address", discover)
    monkeypatch.setattr(atspi_window, "Thread", worker)
    monkeypatch.setattr(
        atspi_window.Gio.DBusConnection, "new_for_address_sync", lambda *_: connection
    )
    monkeypatch.setattr(atspi_window.GLib, "timeout_add", MagicMock(return_value=17))
    monkeypatch.setattr(atspi_window.GLib, "source_remove", MagicMock())
    idle = MagicMock()
    monkeypatch.setattr(atspi_window.GLib, "idle_add", idle)
    service.start()
    try:
        assert entered.wait(2), "start must not wait for discovery on the GTK loop"
        service._schedule_refresh()
        assert len(workers) == 1
        service.stop()
        assert cancellations[0].is_cancelled()
    finally:
        release.set()
        workers[0].join(2)
        service.stop()
    assert not workers[0].is_alive()
    assert service._connection is None
    connection.close_sync.assert_called_once_with(None)
    model.update_running.assert_not_called()
    idle.assert_not_called()
