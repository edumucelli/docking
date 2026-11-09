"""Extra messages installed only by native browser acceptance tools.

Production companions never install this codec and ignore all fixture commands,
including JavaScript probes. Keep diagnostic payloads out of the app protocol.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import IO

from docking.applets.webapp.implementations.windows.companion import protocol

_COMMANDS = {
    "probe",
    "fixture-origin",
    "fixture-downloads",
    "fixture-hang",
    "inspect-service",
}
_EVENTS = {
    "probe",
    "fixture",
    "fixture_origin",
    "fixture_hang",
    "fixture_navigation",
    "service_probe",
}


@dataclass(frozen=True)
class FixtureMessage:
    values: dict[str, object]


def encode(message: protocol.Message | FixtureMessage) -> bytes:
    if not isinstance(message, FixtureMessage):
        return protocol.encode(message)
    data = (
        json.dumps(
            {**message.values, "version": protocol.VERSION}, allow_nan=False
        ).encode()
        + b"\n"
    )
    if len(data) > protocol.MAX_PACKET_BYTES:
        raise ValueError("Browser fixture packet too large")
    return data


def read_command(stream: IO[bytes]) -> protocol.Command | FixtureMessage | None:
    while (packet := protocol._read_packet(stream)) is not None:
        command = protocol.decode_command(packet)
        if command is not None:
            return command
        operation = packet.get("op")
        if isinstance(operation, str) and operation in _COMMANDS:
            packet.pop("version")
            return FixtureMessage(packet)
    return None


def read_event(stream: IO[bytes]) -> protocol.Event | FixtureMessage | None:
    while (packet := protocol._read_packet(stream)) is not None:
        event = protocol.decode_event(packet)
        if event is not None:
            return event
        name = packet.get("event")
        if isinstance(name, str) and name in _EVENTS:
            packet.pop("version")
            return FixtureMessage(packet)
    return None


def fixture_event(event: str, **values: object) -> FixtureMessage:
    return FixtureMessage({"event": event, **values})


def install_parent_codec() -> None:
    from docking.applets.webapp.implementations.windows.companion import process

    process.encode = encode
    process.read_event = read_event


def install_child_codec() -> None:
    from docking.applets.webapp.implementations.windows.companion import runtime

    runtime.encode = encode
    runtime.read_command = read_command
