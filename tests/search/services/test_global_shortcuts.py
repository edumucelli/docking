"""Tests for the isolated XDG GlobalShortcuts service."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast

import pytest

import docking.search.services.implementations.unix.shortcuts as shortcuts_mod
from docking.search.services.implementations.unix.shortcuts import (
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
        on_activated=(
            (lambda _activation: None) if activated is None else activated.append
        ),
        on_status_changed=lambda _status: None,
        preferred_trigger="CTRL+LOGO+space",
    )
    service._connection = _Connection()
    service._call_adapter = calls
    service._signal_adapter = signals
    service._token_factory = lambda: "fixed"
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


class _FailingCalls(_Calls):
    """A call adapter that fails one interface, as a missing portal would."""

    def __init__(self, *, fail_interface: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._fail_interface = fail_interface

    def __call__(self, connection, *, interface, **kwargs):
        if interface == self._fail_interface:
            raise RuntimeError(f"{interface} is not available")
        return super().__call__(connection, interface=interface, **kwargs)


def _service_with(
    *,
    version: int = 1,
    fail_interface: str | None = None,
) -> tuple[GlobalShortcutsService, _Calls, _Signals]:
    calls: _Calls = (
        _Calls(version=version)
        if fail_interface is None
        else _FailingCalls(fail_interface=fail_interface, version=version)
    )
    signals = _Signals()
    service = GlobalShortcutsService(
        app_id="org.docking.Docking",
        on_activated=lambda _activation: None,
        on_status_changed=lambda _status: None,
        preferred_trigger="CTRL+LOGO+space",
    )
    service._connection = _Connection()
    service._call_adapter = calls
    service._signal_adapter = signals
    service._token_factory = lambda: "fixed"
    return service, calls, signals


def test_start_reports_unavailable_when_the_session_bus_has_no_connection() -> None:
    service, _calls, _signals = _service()
    service._connection = None

    def refuse_connection() -> object:
        raise RuntimeError("no session bus")

    service._connection_adapter = refuse_connection

    service.start()

    assert service.state is GlobalShortcutsState.UNAVAILABLE
    assert "session bus unavailable" in (service.status.message or "")


def test_start_is_idempotent_once_running() -> None:
    service, calls, _signals = _service()

    service.start()
    registered = len(calls.by_method("Register"))
    service.start()

    assert len(calls.by_method("Register")) == registered


def test_an_optional_registry_failure_does_not_abort_startup() -> None:
    """Registering with the desktop is best effort and may not exist."""
    service, calls, signals = _service_with(fail_interface=REGISTRY_INTERFACE)

    service.start()

    # The session request was still issued despite the registry refusal.
    assert service.state is GlobalShortcutsState.CREATING
    assert calls.by_method("CreateSession")

    # And the sequence still completes normally.
    _create_session(service, calls, signals)
    _finish_binding(service, calls, signals)
    assert service.state is GlobalShortcutsState.BOUND


def test_an_unreadable_portal_version_reports_unavailable() -> None:
    service, _calls, _signals = _service_with(version="not-a-number")

    service.start()

    assert service.state is GlobalShortcutsState.UNAVAILABLE
    assert "portal unavailable" in (service.status.message or "")


def test_create_session_without_a_usable_handle_is_an_error() -> None:
    for results in ({}, {"session_handle": "relative/path"}, {"session_handle": 7}):
        service, calls, signals = _service()
        service.start()

        signals.respond(calls.request("CreateSession"), 0, results)

        assert service.state is GlobalShortcutsState.ERROR, results
        assert "no session handle" in (service.status.message or "")


def test_a_failed_session_subscription_rolls_the_session_back() -> None:
    service, calls, signals = _service()
    service.start()

    def refuse_signals():
        raise RuntimeError("bus is gone")

    signals.subscribe = refuse_signals  # type: ignore[method-assign]
    signals.respond(calls.request("CreateSession"), 0, {"session_handle": "/s/1"})

    assert service.state is GlobalShortcutsState.ERROR
    assert "could not subscribe to session signals" in (service.status.message or "")
    assert service.session_handle is None
    # The half-open remote session must be closed rather than leaked.
    assert [call.interface for call in calls.by_method("Close")] == [SESSION_INTERFACE]


def test_binding_without_the_toggle_shortcut_is_denied() -> None:
    service, calls, signals = _service()
    service.start()
    _create_session(service, calls, signals)

    signals.respond(calls.request("BindShortcuts"), 0, {"shortcuts": []})

    assert service.state is GlobalShortcutsState.DENIED
    assert "was not bound" in (service.status.message or "")


def test_list_shortcuts_without_the_toggle_shortcut_is_denied() -> None:
    service, calls, signals = _service()
    _start_bound(service, calls, signals)

    service.list_shortcuts()
    signals.respond(calls.request("ListShortcuts"), 0, {"shortcuts": []})

    assert service.state is GlobalShortcutsState.DENIED
    assert "is not assigned" in (service.status.message or "")


def test_a_denied_or_cancelled_response_is_surfaced_verbatim() -> None:
    for response, expected in (
        (1, GlobalShortcutsState.CANCELLED),
        (2, GlobalShortcutsState.DENIED),
    ):
        service, calls, signals = _service()
        service.start()

        signals.respond(calls.request("CreateSession"), response)

        assert service.state is expected, response

    # Codes beyond the documented 0/1/2 are still reported, not swallowed.
    service, calls, signals = _service()
    service.start()
    signals.respond(calls.request("CreateSession"), 9)
    assert service.state is GlobalShortcutsState.ERROR


def test_the_portal_closing_the_session_is_reported_as_an_error() -> None:
    service, calls, signals = _service()
    session_handle = _start_bound(service, calls, signals)

    signals.emit(
        sender=PORTAL_BUS_NAME,
        object_path=session_handle,
        interface=SESSION_INTERFACE,
        signal="Closed",
        parameters=(),
    )

    assert service.state is GlobalShortcutsState.ERROR
    assert "session was closed" in (service.status.message or "")
    assert service.session_handle is None


def test_the_portal_restarting_restarts_the_registration_sequence() -> None:
    service, calls, signals = _service()
    _start_bound(service, calls, signals)

    signals.owner_changed(":1.7", ":1.9")

    # A new owner means a fresh session must be negotiated from scratch.
    assert service.state is GlobalShortcutsState.CREATING
    assert calls.methods().count("CreateSession") == 2


def test_the_portal_disappearing_reports_unavailable() -> None:
    service, calls, signals = _service()
    _start_bound(service, calls, signals)

    signals.owner_changed(":1.7", "")

    assert service.state is GlobalShortcutsState.UNAVAILABLE
    assert "portal stopped" in (service.status.message or "")


def test_signals_for_other_names_sessions_and_arities_are_ignored() -> None:
    service, calls, signals = _service()
    session_handle = _start_bound(service, calls, signals)

    # A change for an unrelated bus name must not disturb the session.
    signals.emit(
        sender=DBUS_BUS_NAME,
        object_path=DBUS_OBJECT_PATH,
        interface=DBUS_INTERFACE,
        signal="NameOwnerChanged",
        parameters=("org.example.Other", ":1.1", ":1.2"),
    )
    # A session-closed event for someone else's session is equally irrelevant.
    signals.emit(
        sender=PORTAL_BUS_NAME,
        object_path="/org/freedesktop/portal/desktop/session/9_9/other",
        interface=SESSION_INTERFACE,
        signal="Closed",
        parameters=(),
    )
    # A malformed ownership payload must not crash the service either.
    service._on_name_owner_changed(("too", "short"))
    service._on_name_owner_changed(("not", "a", "string", "triple"))

    assert service.state is GlobalShortcutsState.BOUND
    assert service.session_handle == session_handle


class _Variant:
    """Stand in for a GLib.Variant, which unpacks to a plain value."""

    def __init__(self, value: object) -> None:
        self._value = value

    def unpack(self) -> object:
        return self._value


class TestPortalPayloadParsing:
    """Portal replies cross a process boundary and are untrusted input."""

    def test_deep_unpack_recurses_through_nested_variants(self):
        nested = _Variant({"a": _Variant((_Variant(1), _Variant([_Variant("x")])))})

        assert shortcuts_mod._deep_unpack(nested) == {"a": (1, ["x"])}

    def test_deep_unpack_passes_plain_values_through(self):
        for value in (5, "text", None, 1.5):
            assert shortcuts_mod._deep_unpack(value) == value

    def test_deep_unpack_handles_a_variant_that_unpacks_to_itself(self):
        class _SelfUnpacking(dict):
            def unpack(self):
                return self

        value = _SelfUnpacking({"k": _Variant(1)})

        assert shortcuts_mod._deep_unpack(value) == {"k": 1}

    def test_vardict_packs_strings_bools_and_signed_ints(self):
        glib = SimpleNamespace(Variant=lambda signature, value: (signature, value))

        packed = shortcuts_mod._gio_vardict(
            glib, {"s": "x", "b": True, "n": 3, "i": -3}
        )

        assert packed == {
            "s": ("s", "x"),
            "b": ("b", True),
            "n": ("u", 3),
            "i": ("i", -3),
        }

    def test_vardict_refuses_values_it_cannot_pack(self):
        glib = SimpleNamespace(Variant=lambda signature, value: (signature, value))

        with pytest.raises(TypeError, match="unsupported variant value for data"):
            shortcuts_mod._gio_vardict(glib, {"data": ["nested"]})

    def test_response_values_reject_malformed_payloads(self):
        for parameters in ((), ("only-one",), (0, {}, 3), ("not-an-int", {})):
            with pytest.raises(ValueError):
                shortcuts_mod._response_values(parameters)

    def test_response_values_unpack_a_well_formed_reply(self):
        response, results = shortcuts_mod._response_values(
            (_Variant(0), _Variant({"shortcuts": []}))
        )

        assert response == 0
        assert results == {"shortcuts": []}

    def test_binding_extraction_skips_malformed_entries(self):
        binding = shortcuts_mod._binding_from_shortcuts(
            [
                "not-a-pair",
                ("too", "many", "fields"),
                ("other-id", {"description": "Other"}),
                (
                    TOGGLE_SEARCH_SHORTCUT_ID,
                    {
                        "description": "Toggle Docking search",
                        "trigger_description": "Super+Space",
                    },
                ),
            ]
        )

        assert binding is not None
        assert binding.shortcut_id == TOGGLE_SEARCH_SHORTCUT_ID
        assert binding.description == "Toggle Docking search"
        assert binding.trigger_description == "Super+Space"

    def test_binding_extraction_returns_none_without_the_toggle_entry(self):
        assert shortcuts_mod._binding_from_shortcuts([("other-id", {})]) is None
        assert shortcuts_mod._binding_from_shortcuts("not-a-sequence") is None

    def test_binding_from_results_tolerates_a_missing_shortcuts_key(self):
        assert shortcuts_mod._binding_from_results({}) is None

    def test_expected_request_path_uses_the_connection_unique_name(self):
        connection = SimpleNamespace(get_unique_name=lambda: ":1.42")

        assert (
            shortcuts_mod._expected_request_path(connection, "tok")
            == "/org/freedesktop/portal/desktop/request/1_42/tok"
        )

    def test_expected_request_path_falls_back_to_the_attribute(self):
        assert shortcuts_mod._expected_request_path(
            SimpleNamespace(unique_name=":1.7"), "tok"
        ) == ("/org/freedesktop/portal/desktop/request/1_7/tok")
        # Without any unique name the expected path cannot be predicted.
        assert shortcuts_mod._expected_request_path(SimpleNamespace(), "tok") is None
        assert (
            shortcuts_mod._expected_request_path(SimpleNamespace(unique_name=""), "tok")
            is None
        )


class TestTokenAndPublish:
    def test_tokens_are_sanitized_for_dbus_and_made_unique(self):
        service, _calls, _signals = _service()
        service._token_factory = lambda: "a b/c:d"

        first = service._new_token("create")
        second = service._new_token("create")

        assert first == "docking_create_1_a_b_c_d"
        assert second == "docking_create_2_a_b_c_d"

    def test_an_empty_token_factory_result_still_yields_a_usable_token(self):
        service, _calls, _signals = _service()
        service._token_factory = lambda: ""

        token = service._new_token("create")

        assert token.startswith("docking_create_1_")
        assert len(token) > len("docking_create_1_")

    def test_publishing_the_same_status_twice_notifies_once(self):
        seen: list[GlobalShortcutsState] = []
        service, _calls, _signals = _service()
        service._on_status_changed = lambda status: seen.append(status.state)

        service._publish(GlobalShortcutsState.STARTING)
        service._publish(GlobalShortcutsState.STARTING)

        assert seen == [GlobalShortcutsState.STARTING]

    def test_a_failing_status_callback_does_not_break_the_service(self):
        def explode(_status):
            raise RuntimeError("listener failed")

        service, calls, _signals = _service()
        service._on_status_changed = explode

        service.start()

        # The portal sequence continues despite the listener blowing up.
        assert calls.by_method("CreateSession")
