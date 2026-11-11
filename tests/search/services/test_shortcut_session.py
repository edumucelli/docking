"""Search and dock actions share a session without sharing their outcomes."""

from unittest.mock import MagicMock

from docking.search.services.global_shortcuts import (
    GlobalShortcutActivation,
    GlobalShortcutBinding,
    GlobalShortcutSpec,
    GlobalShortcutsState,
    GlobalShortcutsStatus,
)
from docking.search.services.implementations.unix import shortcut_session


def test_actions_share_binding_batch_and_reject_old_session_callbacks(monkeypatch):
    pending = {}
    services = []

    def schedule(callback):
        source = max(pending, default=0) + 1
        pending[source] = callback
        return source

    def flush():
        for source, callback in tuple(pending.items()):
            pending.pop(source)
            callback()

    def create(**arguments):
        service = MagicMock()
        service.arguments = arguments
        services.append(service)
        return service

    monkeypatch.setattr(shortcut_session, "GlobalShortcutsService", create)
    session = shortcut_session.PortalShortcutSession()
    activations, statuses = {}, {}
    registrations = {}
    for action in ("toggle-search", "toggle-dock"):
        activations[action], statuses[action] = [], []
        registrations[action] = session.registration(
            app_id="org.docking.Docking",
            spec=GlobalShortcutSpec(action, action, "CTRL+ALT+" + action[-1]),
            on_activated=activations[action].append,
            on_status_changed=statuses[action].append,
            schedule_idle=schedule,
            remove_source=lambda source: pending.pop(source) is not None,
        )
        registrations[action].start()
    assert not services
    flush()
    assert len(services) == 1
    first = services[0]
    assert [spec.shortcut_id for spec in first.arguments["shortcuts"]] == [
        "toggle-search",
        "toggle-dock",
    ]
    search = GlobalShortcutBinding("toggle-search", "Search", "Ctrl+Alt+Space")
    first.arguments["on_status_changed"](
        GlobalShortcutsStatus(
            GlobalShortcutsState.BOUND,
            binding=search,
            bindings=(search,),
        )
    )
    assert registrations["toggle-search"].status.state is GlobalShortcutsState.BOUND
    assert registrations["toggle-dock"].status.state is GlobalShortcutsState.DENIED
    event = GlobalShortcutActivation("toggle-dock", 123, None)
    first.arguments["on_activated"](event)
    assert activations["toggle-dock"] == [event]
    assert not activations["toggle-search"]
    registrations["toggle-dock"].stop()
    first.arguments["on_activated"](event)
    assert activations["toggle-dock"] == [event]
    flush()
    assert len(services) == 2
    first.stop.assert_called_once()
    assert len(services[1].arguments["shortcuts"]) == 1
    registrations["toggle-search"].stop()
    services[1].stop.assert_called_once()
    assert not pending


def test_secondary_binding_can_succeed_when_first_action_is_denied(monkeypatch):
    transport = MagicMock()
    factory = MagicMock(return_value=transport)
    monkeypatch.setattr(shortcut_session, "GlobalShortcutsService", factory)
    callbacks = []
    session = shortcut_session.PortalShortcutSession()
    registrations = []
    for action in ("toggle-search", "toggle-dock"):
        registration = session.registration(
            app_id="org.docking.Docking",
            spec=GlobalShortcutSpec(action, action, "CTRL+ALT+d"),
            on_activated=lambda _: None,
            on_status_changed=lambda _: None,
            schedule_idle=lambda callback: callbacks.append(callback) or len(callbacks),
            remove_source=lambda _: True,
        )
        registrations.append(registration)
        registration.start()
    callbacks[-1]()
    dock = GlobalShortcutBinding("toggle-dock", "Dock", "Ctrl+Alt+D")
    factory.call_args.kwargs["on_status_changed"](
        GlobalShortcutsStatus(
            GlobalShortcutsState.DENIED,
            bindings=(dock,),
        )
    )
    assert registrations[0].status.state is GlobalShortcutsState.DENIED
    assert registrations[1].status.state is GlobalShortcutsState.BOUND
    assert registrations[1].status.binding == dock
