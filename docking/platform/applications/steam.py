"""Local Steam library discovery, without account access or network requests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

STEAM_FLATPAK_ID = "com.valvesoftware.Steam"
MAX_VDF_BYTES = 1024 * 1024
MAX_VDF_DEPTH = 16
_TOKEN = re.compile(r'\s+|//[^\n]*|"(?:\\.|[^"\\])*"|[{}]|[^\s{}"]+')


def valid_steam_app_id(value: str) -> str | None:
    """Normalize a positive 32-bit Steam application ID."""
    if not value.isascii() or not value.isdecimal() or len(value) > 10:
        return None
    number = int(value)
    return str(number) if 0 < number < 2**32 else None


def parse_vdf(text: str) -> dict[str, object]:
    """Parse bounded text KeyValues, rejecting incomplete or malformed input."""
    if len(text.encode("utf-8")) > MAX_VDF_BYTES:
        raise ValueError("Oversized Steam metadata")
    tokens: list[str] = []
    end = 0
    for match in _TOKEN.finditer(text.lstrip("\ufeff")):
        if match.start() != end:
            raise ValueError("Malformed Steam metadata")
        end = match.end()
        token = match.group()
        if token.isspace() or token.startswith("//"):
            continue
        tokens.append(token)
    if end != len(text.lstrip("\ufeff")):
        raise ValueError("Incomplete Steam metadata")
    root: dict[str, object] = {}
    stack = [root]
    key: str | None = None
    for token in tokens:
        if token == "{":
            if key is None or len(stack) >= MAX_VDF_DEPTH:
                raise ValueError("Invalid Steam metadata nesting")
            child: dict[str, object] = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif token == "}":
            if key is not None or len(stack) == 1:
                raise ValueError("Unexpected Steam metadata brace")
            stack.pop()
        else:
            value = token
            if token.startswith('"'):
                value = re.sub(r'\\(["\\])', r"\1", token[1:-1])
            if key is None:
                key = value.casefold()
            else:
                stack[-1][key] = value
                key = None
    if key is not None or len(stack) != 1:
        raise ValueError("Incomplete Steam metadata")
    return root


def _read_vdf(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        data = stream.read(MAX_VDF_BYTES + 1)
    if len(data) > MAX_VDF_BYTES:
        raise ValueError("Oversized Steam metadata")
    return parse_vdf(data.decode("utf-8-sig"))


def steam_app_id_from_exec(exec_line: str) -> str | None:
    """Recognize direct native/Flatpak Steam game launcher commands."""
    from docking.platform.commands import desktop_exec_argv

    try:
        argv = desktop_exec_argv(exec_line)
    except ValueError:
        return None
    if not argv:
        return None
    if Path(argv[0]).name == "flatpak":
        if len(argv) < 3 or argv[1] != "run" or STEAM_FLATPAK_ID not in argv:
            return None
        argv = ["steam", *argv[argv.index(STEAM_FLATPAK_ID) + 1 :]]
    if Path(argv[0]).name != "steam":
        return None
    for index, argument in enumerate(argv[1:], 1):
        if argument.startswith("steam://rungameid/"):
            return valid_steam_app_id(argument.removeprefix("steam://rungameid/"))
        if argument == "-applaunch" and index + 1 < len(argv):
            return valid_steam_app_id(argv[index + 1])
    return None


@dataclass(frozen=True)
class SteamGame:
    app_id: str
    name: str
    install_dir: Path
    manifest: Path
    steam_root: Path
    flatpak: bool = False
    icon: str = ""


@dataclass(frozen=True)
class SteamDiscoveryResult:
    games: tuple[SteamGame, ...] = ()
    watch_directories: tuple[Path, ...] = ()
    issues: tuple[str, ...] = ()
