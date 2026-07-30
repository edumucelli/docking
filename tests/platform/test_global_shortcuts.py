"""Tests for the isolated XDG GlobalShortcuts service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from docking.platform.global_shortcuts import (
    DBUS_BUS_NAME,
    DBUS_INTERFACE,
    DBUS_OBJECT_PATH,
    GLOBAL_SHORTCUTS_INTERFACE,
    PORTAL_BUS_NAME,
    PORTAL_OBJECT_PATH,
    PROPERTIES_INTERFACE,
    REGISTRY_INTERFACE,
    REQUEST_INTERFACE,
    SESSION_INTERFACE,
    TOGGLE_SEARCH_SHORTCUT_ID,
    GlobalShortcutActivation,
    GlobalShortcutsService,
    GlobalShortcutsState,
    SignalCallback,
)


@dataclass(frozen=True)
class _Call:
    object_path: str
    interface: str
    method: str
    arguments: tuple[object, ...]


class _Connection:
    def get_unique_name(self) -> str:
        return ":1.42"


class _Calls:
    def __init__(self, *, version: int = 1) -> None:
        self.version = version
        self.calls: list[_Call] = []
        self.requests: dict[str, list[str]] = {}

    def __call__(
        self,
        connection: object,
        *,
        destination: str,
        object_path: str,
        interface: str,
        method: str,
        arguments: tuple[object, ...] = (),
    ) -> object:
        assert isinstance(connection, _Connection)
        assert destination == PORTAL_BUS_NAME
        self.calls.append(
            _Call(
                object_path=object_path,
                interface=interface,
                method=method,
                arguments=arguments,
            )
        )
        if interface == REGISTRY_INTERFACE and method == "Register":
            return ()
        if interface == PROPERTIES_INTERFACE and method == "Get":
            return (self.version,)
        if interface in {REQUEST_INTERFACE, SESSION_INTERFACE} and method == "Close":
            return ()
        if interface != GLOBAL_SHORTCUTS_INTERFACE:
            raise AssertionError(f"unexpected call: {interface}.{method}")
        if method == "CreateSession":
            options = _dict(arguments[0])
        elif method == "BindShortcuts":
            options = _dict(arguments[3])
        elif method == "ListShortcuts":
            options = _dict(arguments[1])
        else:
            raise AssertionError(f"unexpected GlobalShortcuts method: {method}")

        token = str(options["handle_token"])
        path = f"/org/freedesktop/portal/desktop/request/1_42/{token}"
        self.requests.setdefault(method, []).append(path)
        return (path,)

    def methods(self) -> list[str]:
        return [call.method for call in self.calls]

    def by_method(self, method: str) -> list[_Call]:
        return [call for call in self.calls if call.method == method]

    def request(self, method: str, index: int = -1) -> str:
        return self.requests[method][index]


@dataclass
class _Subscription:
    sender: str
    object_path: str | None
    interface: str
    signal: str
    arg0: str | None
    callback: SignalCallback


class _Signals:
    def __init__(self) -> None:
        self._next_handle = 1
        self.active: dict[int, _Subscription] = {}
        self.unsubscribed: list[int] = []

    def subscribe(
        self,
        connection: object,
        *,
        sender: str,
        object_path: str | None,
        interface: str,
        signal: str,
        arg0: str | None,
        callback,
    ) -> object:
        assert isinstance(connection, _Connection)
        handle = self._next_handle
        self._next_handle += 1
        self.active[handle] = _Subscription(
            sender=sender,
            object_path=object_path,
            interface=interface,
            signal=signal,
            arg0=arg0,
            callback=callback,
        )
        return handle

    def unsubscribe(self, connection: object, handle: object) -> None:
        assert isinstance(connection, _Connection)
        assert isinstance(handle, int)
        self.active.pop(handle, None)
        self.unsubscribed.append(handle)

    def emit(
        self,
        *,
        sender: str,
        object_path: str,
        interface: str,
        signal: str,
        parameters: tuple[object, ...],
    ) -> None:
        for handle, subscription in tuple(self.active.items()):
            if handle not in self.active:
                continue
            if (
                subscription.sender != sender
                or subscription.object_path != object_path
                or subscription.interface != interface
                or subscription.signal != signal
            ):
                continue
            if subscription.arg0 is not None and (
                not parameters or parameters[0] != subscription.arg0
            ):
                continue
            subscription.callback(parameters)

    def respond(
        self,
        path: str,
        response: int,
        results: dict[str, object] | None = None,
    ) -> None:
        self.emit(
            sender=PORTAL_BUS_NAME,
            object_path=path,
            interface=REQUEST_INTERFACE,
            signal="Response",
            parameters=(response, results or {}),
        )

    def owner_changed(self, old_owner: str, new_owner: str) -> None:
        self.emit(
            sender=DBUS_BUS_NAME,
            object_path=DBUS_OBJECT_PATH,
            interface=DBUS_INTERFACE,
            signal="NameOwnerChanged",
            parameters=(PORTAL_BUS_NAME, old_owner, new_owner),
        )


def _dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _service(
    *,
    version: int = 1,
    activated: list[GlobalShortcutActivation] | None = None,
) -> tuple[GlobalShortcutsService, _Calls, _Signals]:
    calls = _Calls(version=version)
    signals = _Signals()
    service = GlobalShortcutsService(
        app_id="org.docking.Docking",
        on_activated=None if activated is None else activated.append,
        preferred_trigger="CTRL+LOGO+space",
        connection=_Connection(),
        call_adapter=calls,
        signal_adapter=signals,
        token_factory=lambda: "fixed",
    )
    return service, calls, signals


def _create_session(
    service: GlobalShortcutsService,
    calls: _Calls,
    signals: _Signals,
    *,
    session_handle: str = "/org/freedesktop/portal/desktop/session/1_42/docking",
) -> str:
    signals.respond(
        calls.request("CreateSession"),
        0,
        {"session_handle": session_handle},
    )
    assert service.state is GlobalShortcutsState.BINDING
    return session_handle


def _finish_binding(
    service: GlobalShortcutsService,
    calls: _Calls,
    signals: _Signals,
    *,
    trigger: str = "Super+Space",
) -> None:
    signals.respond(
        calls.request("BindShortcuts"),
        0,
        {
            "shortcuts": [
                (
                    TOGGLE_SEARCH_SHORTCUT_ID,
                    {
                        "description": "Toggle Docking search",
                        "trigger_description": trigger,
                    },
                )
            ]
        },
    )
    assert service.state is GlobalShortcutsState.BOUND


def _start_bound(
    service: GlobalShortcutsService,
    calls: _Calls,
    signals: _Signals,
) -> str:
    service.start()
    session_handle = _create_session(service, calls, signals)
    _finish_binding(service, calls, signals)
    return session_handle


def test_success_registers_app_creates_session_and_binds_stable_shortcut() -> None:
    service, calls, signals = _service()

    service.start()

    assert calls.methods()[:3] == ["Register", "Get", "CreateSession"]
    register = calls.by_method("Register")[0]
    assert register.arguments == ("org.docking.Docking", {})
    assert service.state is GlobalShortcutsState.CREATING

    _create_session(service, calls, signals)
    bind = calls.by_method("BindShortcuts")[0]
    shortcuts = bind.arguments[1]
    assert shortcuts == (
        (
            TOGGLE_SEARCH_SHORTCUT_ID,
            {
                "description": "Toggle Docking search",
                "preferred_trigger": "CTRL+LOGO+space",
            },
        ),
    )

    _finish_binding(service, calls, signals)
    assert service.status.binding is not None
    assert service.status.binding.shortcut_id == "toggle-search"
    assert service.status.binding.trigger_description == "Super+Space"


def test_bind_denial_is_distinct_from_other_failures() -> None:
    service, calls, signals = _service()
    service.start()
    _create_session(service, calls, signals)

    signals.respond(calls.request("BindShortcuts"), 2)

    assert service.state is GlobalShortcutsState.DENIED
    assert service.status.message == "BindShortcuts was denied"


def test_bind_cancellation_is_distinct_from_denial() -> None:
    service, calls, signals = _service()
    service.start()
    _create_session(service, calls, signals)

    signals.respond(calls.request("BindShortcuts"), 1)

    assert service.state is GlobalShortcutsState.CANCELLED
    assert service.status.message == "BindShortcuts was cancelled"


def test_activated_forwards_activation_token() -> None:
    activations: list[GlobalShortcutActivation] = []
    service, calls, signals = _service(activated=activations)
    session_handle = _start_bound(service, calls, signals)

    signals.emit(
        sender=PORTAL_BUS_NAME,
        object_path=PORTAL_OBJECT_PATH,
        interface=GLOBAL_SHORTCUTS_INTERFACE,
        signal="Activated",
        parameters=(
            session_handle,
            TOGGLE_SEARCH_SHORTCUT_ID,
            9876,
            {"activation_token": "wayland-token"},
        ),
    )

    assert activations == [
        GlobalShortcutActivation(
            shortcut_id="toggle-search",
            timestamp=9876,
            activation_token="wayland-token",
        )
    ]


def test_list_is_gated_by_interface_version() -> None:
    service_v1, calls_v1, signals_v1 = _service(version=1)
    _start_bound(service_v1, calls_v1, signals_v1)

    assert service_v1.supports_list_shortcuts is True
    assert service_v1.list_shortcuts() is True
    assert "ListShortcuts" in calls_v1.methods()

    signals_v1.respond(
        calls_v1.request("ListShortcuts"),
        0,
        {
            "shortcuts": [
                (
                    TOGGLE_SEARCH_SHORTCUT_ID,
                    {"trigger_description": "Super+Space"},
                )
            ]
        },
    )


def test_version_zero_marks_portal_unavailable_without_creating_session() -> None:
    service, calls, _signals = _service(version=0)

    service.start()

    assert service.state is GlobalShortcutsState.UNAVAILABLE
    assert service.portal_version is None
    assert "CreateSession" not in calls.methods()
    assert service.list_shortcuts() is False


def test_shortcuts_changed_marks_an_existing_binding_reassigned() -> None:
    service, calls, signals = _service()
    session_handle = _start_bound(service, calls, signals)

    signals.emit(
        sender=PORTAL_BUS_NAME,
        object_path=PORTAL_OBJECT_PATH,
        interface=GLOBAL_SHORTCUTS_INTERFACE,
        signal="ShortcutsChanged",
        parameters=(
            session_handle,
            [
                (
                    TOGGLE_SEARCH_SHORTCUT_ID,
                    {"trigger_description": "Ctrl+Space"},
                )
            ],
        ),
    )

    assert service.state is GlobalShortcutsState.REASSIGNED
    assert service.status.binding is not None
    assert service.status.binding.trigger_description == "Ctrl+Space"


def test_portal_restart_re_registers_and_recreates_the_session() -> None:
    service, calls, signals = _service()
    _start_bound(service, calls, signals)

    signals.owner_changed(":1.7", "")

    assert service.state is GlobalShortcutsState.UNAVAILABLE
    assert service.session_handle is None
    assert len(calls.by_method("Register")) == 1

    signals.owner_changed("", ":1.8")

    assert service.state is GlobalShortcutsState.CREATING
    assert len(calls.by_method("Register")) == 2
    assert len(calls.by_method("CreateSession")) == 2
    _create_session(
        service,
        calls,
        signals,
        session_handle="/org/freedesktop/portal/desktop/session/1_42/restarted",
    )
    _finish_binding(service, calls, signals)


def test_stop_closes_session_and_unsubscribes_every_signal() -> None:
    activations: list[GlobalShortcutActivation] = []
    service, calls, signals = _service(activated=activations)
    session_handle = _start_bound(service, calls, signals)
    assert len(signals.active) == 4

    service.stop()

    assert service.state is GlobalShortcutsState.STOPPED
    assert service.session_handle is None
    assert signals.active == {}
    close_calls = calls.by_method("Close")
    assert len(close_calls) == 1
    assert close_calls[0].interface == SESSION_INTERFACE
    assert close_calls[0].object_path == session_handle

    signals.emit(
        sender=PORTAL_BUS_NAME,
        object_path=PORTAL_OBJECT_PATH,
        interface=GLOBAL_SHORTCUTS_INTERFACE,
        signal="Activated",
        parameters=(
            session_handle,
            TOGGLE_SEARCH_SHORTCUT_ID,
            1,
            {"activation_token": "ignored"},
        ),
    )
    assert activations == []


def test_stop_closes_an_outstanding_request_and_its_subscription() -> None:
    service, calls, signals = _service()
    service.start()
    request_path = calls.request("CreateSession")
    assert len(signals.active) == 2

    service.stop()

    close_calls = calls.by_method("Close")
    assert len(close_calls) == 1
    assert close_calls[0].interface == REQUEST_INTERFACE
    assert close_calls[0].object_path == request_path
    assert signals.active == {}
