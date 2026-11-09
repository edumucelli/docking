"""Typed messages and bounded UTF-8 framing on private browser process pipes."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, TypeAlias, cast

from docking.applets.telegram.state import TELEGRAM_WEB_HOST
from docking.applets.webapp.profile import BrowserProfile
from docking.applets.webapp.state import BrowserPhase
from docking.applets.whatsapp.state import WHATSAPP_WEB_HOST

MAX_PACKET_BYTES = 65_536
VERSION = 1


@dataclass(frozen=True)
class Configuration:
    profile: BrowserProfile
    directory: Path


@dataclass(frozen=True)
class Show:
    pass


@dataclass(frozen=True)
class Hide:
    pass


@dataclass(frozen=True)
class Reload:
    pass


@dataclass(frozen=True)
class ClearSession:
    request_id: int


@dataclass(frozen=True)
class Stop:
    pass


@dataclass(frozen=True)
class Started:
    pass


@dataclass(frozen=True)
class VisibilityChanged:
    visible: bool


@dataclass(frozen=True)
class PhaseChanged:
    phase: BrowserPhase


@dataclass(frozen=True)
class BridgeMessage:
    raw: str


@dataclass(frozen=True)
class TitleChanged:
    title: str


@dataclass(frozen=True)
class Notification:
    pass


@dataclass(frozen=True)
class AttentionCleared:
    pass


@dataclass(frozen=True)
class OpenExternal:
    uri: str


@dataclass(frozen=True)
class RestartRequested:
    request_id: int


@dataclass(frozen=True)
class SessionCleared:
    request_id: int
    ok: bool


Command: TypeAlias = Show | Hide | Reload | ClearSession | Stop
Event: TypeAlias = (
    Started
    | VisibilityChanged
    | PhaseChanged
    | BridgeMessage
    | TitleChanged
    | Notification
    | AttentionCleared
    | OpenExternal
    | RestartRequested
    | SessionCleared
)
Message: TypeAlias = Configuration | Command | Event


def _configuration(packet: dict[str, object]) -> Configuration:
    profile, directory = packet.get("profile"), packet.get("directory")
    if (
        packet.get("op") != "configure"
        or not isinstance(profile, dict)
        or not isinstance(directory, str)
        or not directory
        or "\0" in directory
    ):
        raise ValueError("Invalid browser configuration")
    keys = {"name", "session_name", "url", "bridge_handler", "bridge_script"}
    if set(profile) != keys:
        raise ValueError("Invalid browser profile")
    profile = cast(dict[str, object], profile)
    fields: dict[str, str] = {}
    for key in keys:
        value = profile[key]
        if not isinstance(value, str) or "\0" in value:
            raise ValueError("Invalid browser profile")
        fields[key] = value
    result = BrowserProfile(**fields)
    expected_host = {
        "whatsapp": WHATSAPP_WEB_HOST,
        "telegram": TELEGRAM_WEB_HOST,
    }.get(result.session_name)
    if (
        result.host != expected_host
        or not result.allows_origin(result.url)
        or len(result.name) > 128
        or not Path(directory).is_absolute()
    ):
        raise ValueError("Invalid browser service origin")
    return Configuration(result, Path(directory))


def _request_id(value: object) -> bool:
    return type(value) is int and 0 < value < 2**31


def decode_command(packet: dict[str, object]) -> Command | None:
    """Ignore unknown or invalid ordinary commands, as the runtime always has."""
    match packet.get("op"):
        case "show":
            return Show()
        case "hide":
            return Hide()
        case "reload":
            return Reload()
        case "stop":
            return Stop()
        case "clear":
            identifier = packet.get("id")
            if isinstance(identifier, int) and _request_id(identifier):
                return ClearSession(identifier)
    return None


def decode_event(packet: dict[str, object]) -> Event | None:
    """Validate field types and limits before events reach either UI thread."""
    value, identifier = packet.get("value"), packet.get("id")
    match packet.get("event"):
        case "started":
            return Started()
        case "visible" if type(value) is bool:
            return VisibilityChanged(value)
        case "phase" if isinstance(value, str):
            try:
                return PhaseChanged(BrowserPhase(value))
            except ValueError:
                return None
        case "bridge" if isinstance(value, str):
            return BridgeMessage(value)
        case "title" if isinstance(value, str) and len(value) <= 4096:
            return TitleChanged(value)
        case "notification":
            return Notification()
        case "attention":
            return AttentionCleared()
        case "external":
            uri = packet.get("uri")
            if isinstance(uri, str) and len(uri) <= 32768:
                return OpenExternal(uri)
        case "restart" | "cleared":
            if isinstance(identifier, int) and _request_id(identifier):
                if packet["event"] == "restart":
                    return RestartRequested(identifier)
                ok = packet.get("ok")
                if type(ok) is bool:
                    return SessionCleared(identifier, ok)
    return None


def encode(message: Message) -> bytes:
    """Serialize typed records using the unchanged version-one wire schema."""
    packet: dict[str, object]
    match message:
        case Configuration(profile, directory):
            packet = {
                "op": "configure",
                "profile": asdict(profile),
                "directory": str(directory),
            }
            _configuration(packet)
        case Show():
            packet = {"op": "show"}
        case Hide():
            packet = {"op": "hide"}
        case Reload():
            packet = {"op": "reload"}
        case ClearSession(identifier):
            packet = {"op": "clear", "id": identifier}
        case Stop():
            packet = {"op": "stop"}
        case Started():
            packet = {"event": "started"}
        case VisibilityChanged(visible):
            packet = {"event": "visible", "value": visible}
        case PhaseChanged(phase):
            packet = {"event": "phase", "value": phase.value}
        case BridgeMessage(raw):
            packet = {"event": "bridge", "value": raw}
        case TitleChanged(title):
            packet = {"event": "title", "value": title}
        case Notification():
            packet = {"event": "notification"}
        case AttentionCleared():
            packet = {"event": "attention"}
        case OpenExternal(uri):
            packet = {"event": "external", "uri": uri}
        case RestartRequested(identifier):
            packet = {"event": "restart", "id": identifier}
        case SessionCleared(identifier, ok):
            packet = {"event": "cleared", "id": identifier, "ok": ok}
        case _:
            raise TypeError("Invalid browser message")
    if not isinstance(message, Configuration):
        valid = decode_command(packet) if "op" in packet else decode_event(packet)
        if valid is None:
            raise ValueError("Invalid browser message fields")
    data = json.dumps({**packet, "version": VERSION}, allow_nan=False).encode() + b"\n"
    if len(data) > MAX_PACKET_BYTES:
        raise ValueError("Browser packet too large")
    return data


def _read_packet(stream: IO[bytes]) -> dict[str, object] | None:
    data = stream.readline(MAX_PACKET_BYTES + 1)
    if not data:
        return None
    if len(data) > MAX_PACKET_BYTES or not data.endswith(b"\n"):
        raise ValueError("Invalid browser packet size")
    packet = json.loads(data)
    if (
        not isinstance(packet, dict)
        or type(packet.get("version")) is not int
        or packet["version"] != VERSION
    ):
        raise ValueError("Invalid browser protocol version")
    # JSON object keys are strings; values are validated by the message decoder.
    return cast(dict[str, object], packet)


def read_configuration(stream: IO[bytes]) -> Configuration | None:
    packet = _read_packet(stream)
    return _configuration(packet) if packet is not None else None


def read_command(stream: IO[bytes]) -> Command | None:
    # An ignored message is distinct from EOF: continue reading after it.
    while (packet := _read_packet(stream)) is not None:
        command = decode_command(packet)
        if command is not None:
            return command
    return None


def read_event(stream: IO[bytes]) -> Event | None:
    while (packet := _read_packet(stream)) is not None:
        event = decode_event(packet)
        if event is not None:
            return event
    return None
