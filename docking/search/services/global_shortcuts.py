"""XDG GlobalShortcuts portal integration for Global Search.

The service in this module deliberately has no non-portal fallback.  In
particular, it never writes compositor or desktop-environment shortcut
settings.  D-Bus transport is kept behind small adapters so the lifecycle can
be tested without importing PyGObject.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
GLOBAL_SHORTCUTS_INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REGISTRY_INTERFACE = "org.freedesktop.host.portal.Registry"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

DBUS_BUS_NAME = "org.freedesktop.DBus"
DBUS_OBJECT_PATH = "/org/freedesktop/DBus"
DBUS_INTERFACE = "org.freedesktop.DBus"

TOGGLE_SEARCH_SHORTCUT_ID = "toggle-search"
GLOBAL_SHORTCUTS_VERSION = 1

log = logging.getLogger(__name__)


class GlobalShortcutsState(str, Enum):
    """Observable lifecycle and outcome states."""

    STOPPED = "stopped"
    STARTING = "starting"
    CREATING = "creating"
    BINDING = "binding"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    DENIED = "denied"
    BOUND = "bound"
    REASSIGNED = "reassigned"
    ERROR = "error"


@dataclass(frozen=True)
class GlobalShortcutBinding:
    """The portal's current presentation of the Docking shortcut."""

    shortcut_id: str
    description: str
    trigger_description: str | None = None


@dataclass(frozen=True)
class GlobalShortcutActivation:
    """An activation forwarded by the portal."""

    shortcut_id: str
    timestamp: int
    activation_token: str | None


@dataclass(frozen=True)
class GlobalShortcutsStatus:
    """A snapshot of service state suitable for UI or diagnostics."""

    state: GlobalShortcutsState
    portal_version: int | None = None
    session_handle: str | None = None
    binding: GlobalShortcutBinding | None = None
    message: str | None = None


SignalCallback: TypeAlias = Callable[[tuple[object, ...]], None]
ActivationCallback: TypeAlias = Callable[[GlobalShortcutActivation], None]
StatusCallback: TypeAlias = Callable[[GlobalShortcutsStatus], None]


class GioConnectionAdapter:
    """Default session-bus connection adapter."""

    def __call__(self) -> object:
        gio, _glib = _load_gio()
        return gio.bus_get_sync(gio.BusType.SESSION, None)


class GioCallAdapter:
    """Default call adapter implemented with ``Gio.DBusConnection``."""

    def __init__(self) -> None:
        self._timeout_ms = 5_000

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
        gio, glib = _load_gio()
        parameters = _gio_parameters(
            glib,
            interface=interface,
            method=method,
            arguments=arguments,
        )
        return connection.call_sync(
            destination,
            object_path,
            interface,
            method,
            parameters,
            None,
            gio.DBusCallFlags.NONE,
            self._timeout_ms,
            None,
        )


class GioSignalAdapter:
    """Default signal adapter implemented with ``Gio.DBusConnection``."""

    def subscribe(
        self,
        connection: object,
        *,
        sender: str,
        object_path: str | None,
        interface: str,
        signal: str,
        arg0: str | None,
        callback: SignalCallback,
    ) -> object:
        gio, _glib = _load_gio()

        def on_signal(
            _connection: object,
            _sender: str,
            _path: str,
            _interface: str,
            _signal: str,
            parameters: object,
        ) -> None:
            unpacked = _deep_unpack(parameters)
            if isinstance(unpacked, tuple):
                callback(unpacked)
            elif isinstance(unpacked, list):
                callback(tuple(unpacked))
            else:
                callback((unpacked,))

        return connection.signal_subscribe(
            sender,
            interface,
            signal,
            object_path,
            arg0,
            gio.DBusSignalFlags.NONE,
            on_signal,
        )

    def unsubscribe(self, connection: object, handle: object) -> None:
        connection.signal_unsubscribe(handle)


@dataclass(eq=False)
class _PendingRequest:
    path: str
    generation: int
    subscription: object | None = None
    done: bool = False


class GlobalShortcutsService:
    """Own one ``toggle-search`` XDG GlobalShortcuts portal session.

    ``start`` initiates the asynchronous CreateSession/BindShortcuts sequence.
    Portal request outcomes arrive through ``status`` and the optional status
    callback.  ``stop`` closes the session and removes every subscription.
    """

    def __init__(
        self,
        *,
        app_id: str,
        on_activated: ActivationCallback,
        on_status_changed: StatusCallback,
        preferred_trigger: str,
    ) -> None:
        if not app_id.strip():
            raise ValueError("app_id must not be empty")
        self._app_id = app_id.strip()
        self._on_activated = on_activated
        self._on_status_changed = on_status_changed
        self._description = "Toggle Docking search"
        self._preferred_trigger = preferred_trigger
        self._parent_window = ""

        self._connection: object | None = None
        self._connection_adapter = GioConnectionAdapter()
        self._call_adapter = GioCallAdapter()
        self._signal_adapter = GioSignalAdapter()
        self._token_factory = lambda: secrets.token_hex(8)

        self._started = False
        self._generation = 0
        self._token_counter = 0
        self._portal_owner: str | None = None
        self._portal_version: int | None = None
        self._session_handle: str | None = None
        self._binding: GlobalShortcutBinding | None = None
        self._owner_subscription: object | None = None
        self._session_subscriptions: list[object] = []
        self._pending_requests: list[_PendingRequest] = []
        self._status = GlobalShortcutsStatus(GlobalShortcutsState.STOPPED)

    @property
    def status(self) -> GlobalShortcutsStatus:
        return self._status

    @property
    def state(self) -> GlobalShortcutsState:
        return self._status.state

    @property
    def portal_version(self) -> int | None:
        return self._portal_version

    @property
    def session_handle(self) -> str | None:
        return self._session_handle

    @property
    def supports_list_shortcuts(self) -> bool:
        return (
            self._portal_version is not None
            and self._portal_version >= GLOBAL_SHORTCUTS_VERSION
        )

    def start(self) -> None:
        """Start watching the portal and create the shortcut session."""
        if self._started:
            return
        self._started = True
        self._publish(GlobalShortcutsState.STARTING)

        try:
            if self._connection is None:
                self._connection = self._connection_adapter()
        except Exception as exc:
            self._publish(
                GlobalShortcutsState.UNAVAILABLE,
                f"session bus unavailable: {exc}",
            )
            return

        try:
            self._owner_subscription = self._subscribe(
                sender=DBUS_BUS_NAME,
                object_path=DBUS_OBJECT_PATH,
                interface=DBUS_INTERFACE,
                signal="NameOwnerChanged",
                arg0=PORTAL_BUS_NAME,
                callback=self._on_name_owner_changed,
            )
        except Exception as exc:
            self._publish(
                GlobalShortcutsState.ERROR,
                f"could not watch portal ownership: {exc}",
            )
            return

        self._initialize_portal()

    def stop(self) -> None:
        """Close the portal session and remove every signal subscription."""
        if not self._started and self.state is GlobalShortcutsState.STOPPED:
            return
        self._started = False
        self._generation += 1
        self._clear_portal_resources(close_remote=True)

        connection = self._connection
        if connection is not None and self._owner_subscription is not None:
            self._unsubscribe(self._owner_subscription)
        self._owner_subscription = None
        self._portal_owner = None
        self._portal_version = None
        self._publish(GlobalShortcutsState.STOPPED)

    def list_shortcuts(self) -> bool:
        """Request the session's bindings when interface version 1 is available."""
        if (
            not self._started
            or self._session_handle is None
            or not self.supports_list_shortcuts
        ):
            return False

        session_handle = self._session_handle
        return self._begin_request(
            method="ListShortcuts",
            make_arguments=lambda token: (
                session_handle,
                {"handle_token": token},
            ),
            on_response=self._on_list_response,
        )

    def _initialize_portal(self) -> None:
        if not self._started or self._connection is None:
            return
        self._generation += 1
        self._portal_version = None
        self._session_handle = None
        self._binding = None
        self._publish(GlobalShortcutsState.STARTING)

        self._register_best_effort()
        try:
            version = self._probe_version()
        except Exception as exc:
            self._publish(
                GlobalShortcutsState.UNAVAILABLE,
                f"GlobalShortcuts portal unavailable: {exc}",
            )
            return
        if version < GLOBAL_SHORTCUTS_VERSION:
            self._publish(
                GlobalShortcutsState.UNAVAILABLE,
                f"unsupported GlobalShortcuts version {version}",
            )
            return

        self._portal_version = version
        self._publish(GlobalShortcutsState.CREATING)
        session_token = self._new_token("session")
        self._begin_request(
            method="CreateSession",
            make_arguments=lambda request_token: (
                {
                    "handle_token": request_token,
                    "session_handle_token": session_token,
                },
            ),
            on_response=self._on_create_response,
        )

    def _register_best_effort(self) -> None:
        try:
            self._call(
                object_path=PORTAL_OBJECT_PATH,
                interface=REGISTRY_INTERFACE,
                method="Register",
                arguments=(self._app_id, {}),
            )
        except Exception as exc:
            # Registry is host-only, optional, and intentionally allowed to vanish.
            log.debug("portal host registration was not available: %s", exc)

    def _probe_version(self) -> int:
        result = self._call(
            object_path=PORTAL_OBJECT_PATH,
            interface=PROPERTIES_INTERFACE,
            method="Get",
            arguments=(GLOBAL_SHORTCUTS_INTERFACE, "version"),
        )
        value = _single_value(_deep_unpack(result))
        try:
            return _integer_value(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid GlobalShortcuts version reply") from exc

    def _on_create_response(self, response: int, results: Mapping[str, object]) -> None:
        if self._publish_response_failure("CreateSession", response):
            return
        session_handle = _string_value(results.get("session_handle"))
        if not session_handle or not session_handle.startswith("/"):
            self._publish(
                GlobalShortcutsState.ERROR,
                "CreateSession returned no session handle",
            )
            return

        self._session_handle = session_handle
        try:
            self._subscribe_session_signals(session_handle)
        except Exception as exc:
            self._close_session_best_effort(session_handle)
            self._session_handle = None
            self._publish(
                GlobalShortcutsState.ERROR,
                f"could not subscribe to session signals: {exc}",
            )
            return

        shortcut_properties: dict[str, object] = {"description": self._description}
        if self._preferred_trigger:
            shortcut_properties["preferred_trigger"] = self._preferred_trigger

        self._publish(GlobalShortcutsState.BINDING)
        self._begin_request(
            method="BindShortcuts",
            make_arguments=lambda token: (
                session_handle,
                ((TOGGLE_SEARCH_SHORTCUT_ID, shortcut_properties),),
                self._parent_window,
                {"handle_token": token},
            ),
            on_response=self._on_bind_response,
        )

    def _on_bind_response(self, response: int, results: Mapping[str, object]) -> None:
        if self._publish_response_failure("BindShortcuts", response):
            return
        binding = _binding_from_results(results)
        if binding is None:
            self._binding = None
            self._publish(
                GlobalShortcutsState.DENIED,
                "the toggle-search shortcut was not bound",
            )
            return
        self._binding = binding
        self._publish(GlobalShortcutsState.BOUND)

    def _on_list_response(self, response: int, results: Mapping[str, object]) -> None:
        if self._publish_response_failure("ListShortcuts", response):
            return
        binding = _binding_from_results(results)
        if binding is None:
            self._binding = None
            self._publish(
                GlobalShortcutsState.DENIED,
                "the toggle-search shortcut is not assigned",
            )
            return
        previous = self._binding
        self._binding = binding
        state = (
            GlobalShortcutsState.REASSIGNED
            if _binding_was_reassigned(previous, binding)
            else GlobalShortcutsState.BOUND
        )
        self._publish(state)

    def _subscribe_session_signals(self, session_handle: str) -> None:
        subscriptions: list[object] = []
        try:
            subscriptions.append(
                self._subscribe(
                    sender=PORTAL_BUS_NAME,
                    object_path=PORTAL_OBJECT_PATH,
                    interface=GLOBAL_SHORTCUTS_INTERFACE,
                    signal="Activated",
                    arg0=None,
                    callback=self._on_activated_signal,
                )
            )
            subscriptions.append(
                self._subscribe(
                    sender=PORTAL_BUS_NAME,
                    object_path=PORTAL_OBJECT_PATH,
                    interface=GLOBAL_SHORTCUTS_INTERFACE,
                    signal="ShortcutsChanged",
                    arg0=None,
                    callback=self._on_shortcuts_changed,
                )
            )
            subscriptions.append(
                self._subscribe(
                    sender=PORTAL_BUS_NAME,
                    object_path=session_handle,
                    interface=SESSION_INTERFACE,
                    signal="Closed",
                    arg0=None,
                    callback=self._on_session_closed,
                )
            )
        except Exception:
            for handle in subscriptions:
                self._unsubscribe(handle)
            raise
        self._session_subscriptions.extend(subscriptions)

    def _on_activated_signal(self, parameters: tuple[object, ...]) -> None:
        values = _deep_unpack(parameters)
        if not isinstance(values, tuple) or len(values) != 4:
            return
        session_handle = _string_value(values[0])
        shortcut_id = _string_value(values[1])
        if (
            session_handle != self._session_handle
            or shortcut_id != TOGGLE_SEARCH_SHORTCUT_ID
        ):
            return
        try:
            timestamp = _integer_value(values[2])
        except (TypeError, ValueError):
            timestamp = 0
        options = _mapping_value(values[3])
        activation_token = _string_value(
            options.get("activation_token", options.get("activation-token"))
        )
        callback = self._on_activated
        try:
            callback(
                GlobalShortcutActivation(
                    shortcut_id=TOGGLE_SEARCH_SHORTCUT_ID,
                    timestamp=timestamp,
                    activation_token=activation_token,
                )
            )
        except Exception as exc:
            self._publish(
                GlobalShortcutsState.ERROR,
                f"activation callback failed: {exc}",
            )

    def _on_shortcuts_changed(self, parameters: tuple[object, ...]) -> None:
        values = _deep_unpack(parameters)
        if not isinstance(values, tuple) or len(values) != 2:
            return
        if _string_value(values[0]) != self._session_handle:
            return
        binding = _binding_from_shortcuts(values[1])
        if binding is None:
            self._binding = None
            self._publish(
                GlobalShortcutsState.DENIED,
                "the toggle-search shortcut is no longer assigned",
            )
            return
        previous = self._binding
        self._binding = binding
        state = (
            GlobalShortcutsState.REASSIGNED
            if _binding_was_reassigned(previous, binding)
            else GlobalShortcutsState.BOUND
        )
        self._publish(state)

    def _on_session_closed(self, _parameters: tuple[object, ...]) -> None:
        if not self._started or self._session_handle is None:
            return
        self._generation += 1
        self._clear_portal_resources(close_remote=False)
        self._publish(
            GlobalShortcutsState.ERROR,
            "the GlobalShortcuts session was closed",
        )

    def _on_name_owner_changed(self, parameters: tuple[object, ...]) -> None:
        values = _deep_unpack(parameters)
        if not isinstance(values, tuple) or len(values) != 3 or not self._started:
            return
        name = _string_value(values[0])
        old_owner = _string_value(values[1]) or ""
        new_owner = _string_value(values[2]) or ""
        if name != PORTAL_BUS_NAME:
            return

        self._portal_owner = new_owner or None
        if not new_owner:
            if old_owner:
                self._generation += 1
                self._clear_portal_resources(close_remote=False)
                self._portal_version = None
                self._publish(
                    GlobalShortcutsState.UNAVAILABLE,
                    "desktop portal stopped",
                )
            return

        if old_owner and old_owner != new_owner:
            self._generation += 1
            self._clear_portal_resources(close_remote=False)
            self._portal_version = None
            self._initialize_portal()
            return

        # A D-Bus activation can announce the initial owner after our
        # synchronous version probe.  Do not tear down a sequence already
        # started in that case.
        if (
            self._portal_version is None
            and self._session_handle is None
            and not self._pending_requests
        ):
            self._initialize_portal()

    def _begin_request(
        self,
        *,
        method: str,
        make_arguments: Callable[[str], tuple[object, ...]],
        on_response: Callable[[int, Mapping[str, object]], None],
    ) -> bool:
        if self._connection is None:
            self._publish(
                GlobalShortcutsState.ERROR,
                f"{method} has no D-Bus connection",
            )
            return False

        token = self._new_token(method.lower())
        expected_path = _expected_request_path(self._connection, token)
        pending = _PendingRequest(
            path=expected_path or "",
            generation=self._generation,
        )
        self._pending_requests.append(pending)

        def receive(parameters: tuple[object, ...]) -> None:
            if pending.done:
                return
            pending.done = True
            if pending in self._pending_requests:
                self._pending_requests.remove(pending)
            if pending.subscription is not None:
                self._unsubscribe(pending.subscription)
                pending.subscription = None
            if not self._started or pending.generation != self._generation:
                return
            try:
                response, results = _response_values(parameters)
            except (TypeError, ValueError) as exc:
                self._publish(
                    GlobalShortcutsState.ERROR,
                    f"{method} returned an invalid response: {exc}",
                )
                return
            on_response(response, results)

        if expected_path:
            try:
                pending.subscription = self._subscribe_request(expected_path, receive)
            except Exception as exc:
                self._discard_pending(pending)
                self._publish(
                    GlobalShortcutsState.ERROR,
                    f"could not watch {method} response: {exc}",
                )
                return False

        try:
            reply = self._call(
                object_path=PORTAL_OBJECT_PATH,
                interface=GLOBAL_SHORTCUTS_INTERFACE,
                method=method,
                arguments=make_arguments(token),
            )
        except Exception as exc:
            self._discard_pending(pending)
            self._publish(
                GlobalShortcutsState.ERROR,
                f"{method} failed: {exc}",
            )
            return False

        # A deterministic adapter may deliver Response from inside the call.
        if pending.done:
            return True

        returned_path = _string_value(_single_value(_deep_unpack(reply)))
        if not returned_path or not returned_path.startswith("/"):
            self._discard_pending(pending)
            self._publish(
                GlobalShortcutsState.ERROR,
                f"{method} returned no request handle",
            )
            return False

        if pending.subscription is None or returned_path != pending.path:
            try:
                replacement = self._subscribe_request(returned_path, receive)
            except Exception as exc:
                self._discard_pending(pending)
                self._publish(
                    GlobalShortcutsState.ERROR,
                    f"could not watch {method} response: {exc}",
                )
                return False
            previous = pending.subscription
            pending.subscription = replacement
            pending.path = returned_path
            if previous is not None:
                self._unsubscribe(previous)
        return True

    def _publish_response_failure(self, operation: str, response: int) -> bool:
        if response == 0:
            return False
        if response == 1:
            self._publish(
                GlobalShortcutsState.CANCELLED,
                f"{operation} was cancelled",
            )
        elif response == 2:
            self._publish(
                GlobalShortcutsState.DENIED,
                f"{operation} was denied",
            )
        else:
            self._publish(
                GlobalShortcutsState.ERROR,
                f"{operation} failed with response {response}",
            )
        return True

    def _clear_portal_resources(self, *, close_remote: bool) -> None:
        pending_requests = tuple(self._pending_requests)
        if close_remote:
            for pending in pending_requests:
                if pending.path:
                    self._close_request_best_effort(pending.path)
        for pending in pending_requests:
            self._discard_pending(pending)

        session_handle = self._session_handle
        if close_remote and session_handle is not None:
            self._close_session_best_effort(session_handle)
        for handle in tuple(self._session_subscriptions):
            self._unsubscribe(handle)
        self._session_subscriptions.clear()
        self._session_handle = None
        self._binding = None

    def _discard_pending(self, pending: _PendingRequest) -> None:
        pending.done = True
        if pending in self._pending_requests:
            self._pending_requests.remove(pending)
        if pending.subscription is not None:
            self._unsubscribe(pending.subscription)
            pending.subscription = None

    def _close_request_best_effort(self, request_path: str) -> None:
        try:
            self._call(
                object_path=request_path,
                interface=REQUEST_INTERFACE,
                method="Close",
            )
        except Exception:
            log.debug("could not close portal request %s", request_path)

    def _close_session_best_effort(self, session_handle: str) -> None:
        try:
            self._call(
                object_path=session_handle,
                interface=SESSION_INTERFACE,
                method="Close",
            )
        except Exception:
            log.debug("could not close portal session %s", session_handle)

    def _call(
        self,
        *,
        object_path: str,
        interface: str,
        method: str,
        arguments: tuple[object, ...] = (),
    ) -> object:
        if self._connection is None:
            raise RuntimeError("session bus is not connected")
        return self._call_adapter(
            self._connection,
            destination=PORTAL_BUS_NAME,
            object_path=object_path,
            interface=interface,
            method=method,
            arguments=arguments,
        )

    def _subscribe_request(self, path: str, callback: SignalCallback) -> object:
        return self._subscribe(
            sender=PORTAL_BUS_NAME,
            object_path=path,
            interface=REQUEST_INTERFACE,
            signal="Response",
            arg0=None,
            callback=callback,
        )

    def _subscribe(
        self,
        *,
        sender: str,
        object_path: str | None,
        interface: str,
        signal: str,
        arg0: str | None,
        callback: SignalCallback,
    ) -> object:
        if self._connection is None:
            raise RuntimeError("session bus is not connected")
        return self._signal_adapter.subscribe(
            self._connection,
            sender=sender,
            object_path=object_path,
            interface=interface,
            signal=signal,
            arg0=arg0,
            callback=callback,
        )

    def _unsubscribe(self, handle: object) -> None:
        if self._connection is None:
            return
        try:
            self._signal_adapter.unsubscribe(self._connection, handle)
        except Exception:
            log.debug("could not remove D-Bus signal subscription")

    def _new_token(self, purpose: str) -> str:
        self._token_counter += 1
        raw = str(self._token_factory())
        safe = "".join(
            character if character.isalnum() or character == "_" else "_"
            for character in raw
        )
        if not safe:
            safe = secrets.token_hex(8)
        return f"docking_{purpose}_{self._token_counter}_{safe}"

    def _publish(
        self,
        state: GlobalShortcutsState,
        message: str | None = None,
    ) -> None:
        status = GlobalShortcutsStatus(
            state=state,
            portal_version=self._portal_version,
            session_handle=self._session_handle,
            binding=self._binding,
            message=message,
        )
        if status == self._status:
            return
        self._status = status
        callback = self._on_status_changed
        try:
            callback(status)
        except Exception:
            log.exception("global shortcut status callback failed")


def _load_gio() -> tuple[object, object]:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    return Gio, GLib


def _gio_parameters(
    glib: object,
    *,
    interface: str,
    method: str,
    arguments: tuple[object, ...],
) -> object | None:
    variant = glib.Variant
    if interface in {REQUEST_INTERFACE, SESSION_INTERFACE} and method == "Close":
        return None
    if interface == PROPERTIES_INTERFACE and method == "Get":
        return variant("(ss)", arguments)
    if interface == REGISTRY_INTERFACE and method == "Register":
        app_id, options = arguments
        return variant(
            "(sa{sv})",
            (app_id, _gio_vardict(glib, _mapping_value(options))),
        )
    if interface != GLOBAL_SHORTCUTS_INTERFACE:
        raise ValueError(f"unsupported D-Bus call {interface}.{method}")
    if method == "CreateSession":
        return variant(
            "(a{sv})",
            (_gio_vardict(glib, _mapping_value(arguments[0])),),
        )
    if method == "BindShortcuts":
        session_handle, shortcuts, parent_window, options = arguments
        packed_shortcuts = tuple(
            (
                _string_value(item[0]) or "",
                _gio_vardict(glib, _mapping_value(item[1])),
            )
            for item in _sequence_value(shortcuts)
            if isinstance(item, tuple) and len(item) == 2
        )
        return variant(
            "(oa(sa{sv})sa{sv})",
            (
                session_handle,
                packed_shortcuts,
                parent_window,
                _gio_vardict(glib, _mapping_value(options)),
            ),
        )
    if method == "ListShortcuts":
        session_handle, options = arguments
        return variant(
            "(oa{sv})",
            (
                session_handle,
                _gio_vardict(glib, _mapping_value(options)),
            ),
        )
    raise ValueError(f"unsupported GlobalShortcuts method {method}")


def _gio_vardict(glib: object, values: Mapping[str, object]) -> dict[str, object]:
    variant = glib.Variant
    packed: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, str):
            packed[key] = variant("s", value)
        elif isinstance(value, bool):
            packed[key] = variant("b", value)
        elif isinstance(value, int):
            packed[key] = variant("u" if value >= 0 else "i", value)
        else:
            raise TypeError(f"unsupported variant value for {key}")
    return packed


def _deep_unpack(value: object) -> object:
    unpack = getattr(value, "unpack", None)
    if callable(unpack):
        unpacked = unpack()
        if unpacked is not value:
            return _deep_unpack(unpacked)
    if isinstance(value, Mapping):
        return {str(key): _deep_unpack(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_deep_unpack(item) for item in value)
    if isinstance(value, list):
        return [_deep_unpack(item) for item in value]
    return value


def _single_value(value: object) -> object:
    if isinstance(value, tuple) and len(value) == 1:
        return _single_value(value[0])
    if isinstance(value, list) and len(value) == 1:
        return _single_value(value[0])
    return value


def _mapping_value(value: object) -> Mapping[str, object]:
    unpacked = _deep_unpack(value)
    if not isinstance(unpacked, Mapping):
        return {}
    return {str(key): item for key, item in unpacked.items()}


def _sequence_value(value: object) -> Sequence[object]:
    unpacked = _deep_unpack(value)
    if isinstance(unpacked, Sequence) and not isinstance(
        unpacked, (str, bytes, bytearray)
    ):
        return unpacked
    return ()


def _string_value(value: object) -> str | None:
    unpacked = _deep_unpack(value)
    if unpacked is None:
        return None
    text = str(unpacked)
    return text if text else None


def _integer_value(value: object) -> int:
    unpacked = _deep_unpack(value)
    if isinstance(unpacked, int):
        return unpacked
    if isinstance(unpacked, (str, bytes, bytearray)):
        return int(unpacked)
    raise TypeError("value is not an integer")


def _binding_from_results(
    results: Mapping[str, object],
) -> GlobalShortcutBinding | None:
    return _binding_from_shortcuts(results.get("shortcuts", ()))


def _binding_was_reassigned(
    previous: GlobalShortcutBinding | None,
    current: GlobalShortcutBinding,
) -> bool:
    return (
        previous is not None
        and previous.trigger_description != current.trigger_description
    )


def _binding_from_shortcuts(value: object) -> GlobalShortcutBinding | None:
    for item in _sequence_value(value):
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        shortcut_id = _string_value(item[0])
        if shortcut_id != TOGGLE_SEARCH_SHORTCUT_ID:
            continue
        properties = _mapping_value(item[1])
        return GlobalShortcutBinding(
            shortcut_id=TOGGLE_SEARCH_SHORTCUT_ID,
            description=_string_value(properties.get("description")) or "",
            trigger_description=_string_value(properties.get("trigger_description")),
        )
    return None


def _response_values(
    parameters: tuple[object, ...],
) -> tuple[int, Mapping[str, object]]:
    values = _deep_unpack(parameters)
    if not isinstance(values, tuple) or len(values) != 2:
        raise ValueError("Response must contain code and results")
    try:
        response = _integer_value(values[0])
    except (TypeError, ValueError) as exc:
        raise ValueError("Response code is not an integer") from exc
    results = _mapping_value(values[1])
    return response, results


def _expected_request_path(connection: object, token: str) -> str | None:
    get_unique_name = getattr(connection, "get_unique_name", None)
    if callable(get_unique_name):
        unique_name = _string_value(get_unique_name())
    else:
        unique_name = _string_value(getattr(connection, "unique_name", None))
    if not unique_name:
        return None
    sender = unique_name.removeprefix(":").replace(".", "_")
    return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
