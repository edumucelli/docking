"""Bounded UTF-8 packets on private browser process pipes."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, Any

from docking.applets.telegram.state import TELEGRAM_WEB_HOST
from docking.applets.whatsapp.state import WHATSAPP_WEB_HOST

from ..profile import BrowserProfile

MAX_PACKET_BYTES = 65_536
VERSION = 1


def encode(packet: dict[str, Any]) -> bytes:
    data = json.dumps({**packet, "version": VERSION}, allow_nan=False).encode() + b"\n"
    if len(data) > MAX_PACKET_BYTES:
        raise ValueError("Browser packet too large")
    return data


def read(stream: IO[bytes]) -> dict[str, Any] | None:
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
    return packet


@dataclass(frozen=True)
class Configuration:
    profile: BrowserProfile
    directory: Path

    def packet(self) -> dict[str, Any]:
        return {
            "op": "configure",
            "profile": asdict(self.profile),
            "directory": str(self.directory),
        }

    @classmethod
    def parse(cls, packet: dict[str, Any]) -> Configuration:
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
        if set(profile) != keys or any(
            not isinstance(value, str) or "\0" in value for value in profile.values()
        ):
            raise ValueError("Invalid browser profile")
        result = BrowserProfile(**profile)
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
        return cls(result, Path(directory))
