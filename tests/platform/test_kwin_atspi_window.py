from __future__ import annotations

from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.applications.types import ApplicationMatch
from docking.platform.backends.kwin import atspi_window
from tests.platform.application_fakes import identity_services


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
    service.stop()
    service.stop()

    connect.assert_called_once()
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
    connection = SimpleNamespace(call_sync=MagicMock(return_value=result))
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
