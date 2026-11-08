"""Parse Flatpak identities without executing launchers or shell commands."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

_APP_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*){2,}")
_FLAGS = frozenset(
    {
        "--user",
        "--system",
        "--file-forwarding",
        "--verbose",
        "-v",
        "--no-a11y-bus",
        "--no-documents-portal",
        "--no-session-bus",
        "--no-system-bus",
        "--devel",
        "--die-with-parent",
        "--sandbox",
    }
)
_VALUE_OPTIONS = frozenset(
    {
        "--arch",
        "--branch",
        "--command",
        "--installation",
        "--env",
        "--filesystem",
        "--nofilesystem",
        "--socket",
        "--nosocket",
        "--device",
        "--nodevice",
        "--share",
        "--unshare",
        "--talk-name",
        "--no-talk-name",
        "--own-name",
        "--system-talk-name",
        "--cwd",
        "--app-path",
        "--runtime",
        "--runtime-version",
    }
)


def valid_flatpak_app_id(value: str) -> bool:
    return _APP_ID.fullmatch(value) is not None


def flatpak_app_id_from_exec(exec_line: str) -> str:
    """Extract the application ref from a direct flatpak run launcher."""
    try:
        argv = shlex.split(exec_line)
    except ValueError:
        return ""
    if not argv:
        return ""
    if Path(argv[0]).name == "env":
        argv = argv[1:]
        while argv and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[0]):
            argv = argv[1:]
    if len(argv) < 3 or Path(argv[0]).name != "flatpak" or argv[1] != "run":
        return ""
    index = 2
    while index < len(argv):
        token = argv[index]
        if token == "--":
            index += 1
            break
        if token in _FLAGS or (token.startswith("--") and "=" in token):
            index += 1
        elif token in _VALUE_OPTIONS:
            index += 2
        elif token.startswith("-"):
            return ""
        else:
            break
    if index >= len(argv):
        return ""
    ref = argv[index].split("/")
    if ref[0] == "runtime":
        return ""
    app_id = ref[1] if ref[0] == "app" and len(ref) > 1 else ref[0]
    return app_id if valid_flatpak_app_id(app_id) else ""
