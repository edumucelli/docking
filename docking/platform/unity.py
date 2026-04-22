"""Unity LauncherEntry integration for per-application dock overlays."""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger
from docking.platform.model import DockModel, LauncherEntryState

log = get_logger(name="unity")

UNITY_BUS_NAME = "com.canonical.Unity"
LAUNCHER_ENTRY_IFACE = "com.canonical.Unity.LauncherEntry"
UPDATE_SIGNAL = "Update"
DBUS_IFACE = "org.freedesktop.DBus"
NAME_OWNER_CHANGED = "NameOwnerChanged"
DBUS_OBJECT_PATH = "/org/freedesktop/DBus"
PAYLOAD_SIGNATURE = "(sa{sv})"
APP_URI_PREFIX = "application://"
THROTTLE_FAST_COUNT = 3
THROTTLE_WINDOW_MS = 32
THROTTLE_WINDOW_US = THROTTLE_WINDOW_MS * 1000


def _unpack(value: object) -> object:
    """Recursively unpack GLib.Variant-like values into Python primitives."""
    if isinstance(value, dict):
        return {str(key): _unpack(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unpack(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_unpack(item) for item in value)
    unpack = getattr(value, "unpack", None)
    if callable(unpack):
        try:
            return _unpack(unpack())
        except Exception:
            return value
    return value


def _as_bool(value: object, *, default: bool = False) -> bool:
    unpacked = _unpack(value)
    return unpacked if isinstance(unpacked, bool) else default


def _as_int(value: object, *, default: int = 0) -> int:
    unpacked = _unpack(value)
    if isinstance(unpacked, bool):
        return int(unpacked)
    if isinstance(unpacked, int):
        return unpacked
    try:
        return int(str(unpacked).strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: object, *, default: float = 0.0) -> float:
    unpacked = _unpack(value)
    if isinstance(unpacked, (int, float)) and not isinstance(unpacked, bool):
        return float(unpacked)
    try:
        return float(str(unpacked).strip())
    except (TypeError, ValueError):
        return default


def parse_application_uri(app_uri: str) -> str | None:
    """Return desktop_id from application:// URI, or None if invalid."""
    if not app_uri.startswith(APP_URI_PREFIX):
        return None
    desktop_id = app_uri[len(APP_URI_PREFIX) :].strip()
    if not desktop_id or "/" in desktop_id or not desktop_id.endswith(".desktop"):
        return None
    return desktop_id


@dataclass
class _SenderEntry:
    state: LauncherEntryState | None = None
    fast_count: int = 0
    last_update_us: int = 0
    throttle_source_id: int = 0
    retry_source_id: int = 0
    warned: bool = False


class UnityLauncherListener:
    """Listen for Unity LauncherEntry updates and feed them into DockModel."""

    def __init__(self, *, model: DockModel) -> None:
        self._model = model
        self._connection: Gio.DBusConnection | None = None
        self._owner_id: int = 0
        self._update_signal_id: int = 0
        self._name_owner_changed_signal_id: int = 0
        self._entries: dict[str, _SenderEntry] = {}

    def start(self) -> None:
        """Subscribe to the session bus."""
        if self._connection is not None:
            return
        try:
            self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as exc:
            log.warning("Could not connect session bus for Unity integration: %s", exc)
            return

        try:
            self._owner_id = Gio.bus_own_name(
                Gio.BusType.SESSION,
                UNITY_BUS_NAME,
                Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT,
                None,
                None,
                None,
            )
            self._update_signal_id = self._connection.signal_subscribe(
                None,
                LAUNCHER_ENTRY_IFACE,
                None,
                None,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_signal,
            )
            self._name_owner_changed_signal_id = self._connection.signal_subscribe(
                DBUS_IFACE,
                DBUS_IFACE,
                NAME_OWNER_CHANGED,
                DBUS_OBJECT_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_name_owner_changed,
            )
        except Exception as exc:
            log.warning("Could not subscribe to Unity LauncherEntry signals: %s", exc)
            self.stop()

    def stop(self) -> None:
        """Unsubscribe from the session bus and cancel deferred work."""
        for sender_name in list(self._entries):
            self._cancel_sender(sender_name=sender_name)
        self._entries.clear()

        if self._connection is not None:
            if self._update_signal_id > 0:
                self._connection.signal_unsubscribe(self._update_signal_id)
            if self._name_owner_changed_signal_id > 0:
                self._connection.signal_unsubscribe(self._name_owner_changed_signal_id)
        self._update_signal_id = 0
        self._name_owner_changed_signal_id = 0

        if self._owner_id > 0:
            Gio.bus_unown_name(self._owner_id)
        self._owner_id = 0
        self._connection = None

    def _cancel_sender(self, *, sender_name: str) -> None:
        entry = self._entries.get(sender_name)
        if entry is None:
            return
        if entry.throttle_source_id > 0:
            GLib.source_remove(entry.throttle_source_id)
            entry.throttle_source_id = 0
        if entry.retry_source_id > 0:
            GLib.source_remove(entry.retry_source_id)
            entry.retry_source_id = 0

    def _parse_payload(
        self,
        *,
        sender_name: str,
        parameters: GLib.Variant | None,
    ) -> LauncherEntryState | None:
        if parameters is None or parameters.get_type_string() != PAYLOAD_SIGNATURE:
            if parameters is not None:
                log.debug(
                    "Ignoring LauncherEntry payload with signature %s from %s",
                    parameters.get_type_string(),
                    sender_name,
                )
            return None
        unpacked = _unpack(parameters)
        if not isinstance(unpacked, tuple) or len(unpacked) != 2:
            return None
        app_uri, props = unpacked
        if not isinstance(app_uri, str) or not isinstance(props, dict):
            return None
        return LauncherEntryState(
            sender_name=sender_name,
            app_uri=app_uri,
            desktop_id=parse_application_uri(app_uri),
            badge_count=max(0, _as_int(props.get("count"), default=0)),
            badge_visible=_as_bool(props.get("count-visible"), default=False),
            progress=max(0.0, min(1.0, _as_float(props.get("progress"), default=0.0))),
            progress_visible=_as_bool(props.get("progress-visible"), default=False),
            urgent=_as_bool(props.get("urgent"), default=False),
        )

    def _perform_update(self, *, sender_name: str) -> None:
        entry = self._entries.get(sender_name)
        if entry is None or entry.state is None:
            return
        applied = self._model.apply_launcher_entry(
            sender_name=sender_name,
            app_uri=entry.state.app_uri,
            state=entry.state,
            create_transient=False,
        )
        if applied:
            if entry.retry_source_id > 0:
                GLib.source_remove(entry.retry_source_id)
                entry.retry_source_id = 0
            return
        if entry.retry_source_id <= 0:
            entry.retry_source_id = GLib.idle_add(self._retry_update, sender_name)

    def _retry_update(self, sender_name: str) -> bool:
        entry = self._entries.get(sender_name)
        if entry is None:
            return False
        entry.retry_source_id = 0
        if entry.state is None:
            return False
        self._model.apply_launcher_entry(
            sender_name=sender_name,
            app_uri=entry.state.app_uri,
            state=entry.state,
            create_transient=True,
        )
        return False

    def _flush_throttled_sender(self, sender_name: str) -> bool:
        entry = self._entries.get(sender_name)
        if entry is None:
            return False
        entry.throttle_source_id = 0
        entry.fast_count = 0
        entry.last_update_us = GLib.get_monotonic_time()
        self._perform_update(sender_name=sender_name)
        return False

    def _handle_update(
        self, *, sender_name: str, parameters: GLib.Variant | None
    ) -> None:
        state = self._parse_payload(sender_name=sender_name, parameters=parameters)
        if state is None:
            return

        now_us = GLib.get_monotonic_time()
        entry = self._entries.setdefault(sender_name, _SenderEntry())
        entry.state = state

        if now_us - entry.last_update_us >= THROTTLE_WINDOW_US:
            entry.fast_count = 0

        if (
            now_us - entry.last_update_us < THROTTLE_WINDOW_US
            and entry.fast_count > THROTTLE_FAST_COUNT
        ):
            if entry.throttle_source_id <= 0:
                if not entry.warned:
                    log.warning(
                        "LauncherEntry sender %s is updating too quickly; deferring",
                        sender_name,
                    )
                    entry.warned = True
                entry.throttle_source_id = GLib.timeout_add(
                    THROTTLE_WINDOW_MS,
                    self._flush_throttled_sender,
                    sender_name,
                )
            return

        entry.fast_count += 1
        entry.last_update_us = now_us
        self._perform_update(sender_name=sender_name)

    def _on_signal(
        self,
        _connection: Gio.DBusConnection,
        sender_name: str,
        _object_path: str,
        _interface_name: str,
        signal_name: str,
        parameters: GLib.Variant,
    ) -> None:
        if signal_name != UPDATE_SIGNAL or not sender_name:
            return
        self._handle_update(sender_name=sender_name, parameters=parameters)

    def _on_name_owner_changed(
        self,
        _connection: Gio.DBusConnection,
        _sender_name: str,
        _object_path: str,
        _interface_name: str,
        _signal_name: str,
        parameters: GLib.Variant,
    ) -> None:
        unpacked = _unpack(parameters)
        if (
            not isinstance(unpacked, tuple)
            or len(unpacked) != 3
            or not isinstance(unpacked[0], str)
        ):
            return
        name, _before, after = unpacked
        if after:
            return
        self._cancel_sender(sender_name=name)
        self._entries.pop(name, None)
        self._model.remove_launcher_entry(sender_name=name)
