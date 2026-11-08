"""Executable command identity parsing shared by application matching."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from docking.platform.commands import desktop_exec_argv

_GENERATED_EXEC_PREFIX = ("/bin/sh", "-c", 'exec "$@"', "docking-generated")


def _executable_token(exec_line: str) -> str:
    try:
        argv = desktop_exec_argv(exec_line)
    except ValueError:
        return ""
    if tuple(argv[:4]) == _GENERATED_EXEC_PREFIX and len(argv) >= 5:
        return argv[4]
    return argv[0] if argv else ""


def normalized_exec_basename(exec_line: str) -> str:
    """Return the lowercase executable basename from a desktop Exec line."""
    executable = _executable_token(exec_line)
    return Path(executable).name.lower() if executable else ""


def script_launcher_basename(exec_line: str) -> str:
    """Identify a script launcher, including a simple pkexec command wrapper.

    This is matching evidence only: callers must preserve the original Exec
    line for launching and executable-path comparisons.
    """
    try:
        argv = desktop_exec_argv(exec_line)
    except ValueError:
        return ""
    if not argv or Path(argv[0]).name != "pkexec":
        return normalized_exec_basename(exec_line)
    command = argv[1:]
    if command and command[0] == "--":
        command = command[1:]
    if not command or command[0].startswith("-"):
        return ""
    return Path(command[0]).name.lower()


def executable_path_from_exec_line(exec_line: str) -> Path | None:
    """Return a canonical direct executable path from a desktop Exec line."""
    executable = _executable_token(exec_line)
    if not executable:
        return None
    try:
        path = Path(executable).expanduser()
        if not path.is_absolute():
            return None
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_file() else None


def wine_executable_aliases(exec_line: str) -> list[str]:
    """Return Wine executable aliases from a desktop Exec line."""
    if not exec_line:
        return []

    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        tokens = re.findall(r"[^\s\"']+|\"[^\"]*\"|'[^']*'", exec_line)

    has_wine = any(Path(token).name.lower() in {"wine", "wine64"} for token in tokens)
    if not has_wine:
        return []

    aliases: list[str] = []
    for match in re.finditer(
        r"""(?ix)
        (?:
            "([^"]+?\.exe)"
            |
            '([^']+?\.exe)'
            |
            ([^\s"']+?\.exe)
        )
        """,
        exec_line,
    ):
        executable = next((group for group in match.groups() if group), "")
        executable = executable.strip().strip("\"'")
        if not executable:
            continue
        basename = re.split(r"[\\/]", executable)[-1].lower()
        if not basename.endswith(".exe"):
            continue
        aliases.append(basename)
        aliases.append(basename[:-4])
    return list(dict.fromkeys(alias for alias in aliases if alias))
