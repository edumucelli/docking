"""Generic shell command and terminal launch helpers."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from enum import Enum
from functools import cache
from typing import NamedTuple

from docking.log import get_logger, with_context
from docking.platform.environment import flatpak

log = with_context(get_logger(name="commands"))

TERMINAL_LOOKUP_TIMEOUT_SECONDS = 1.5


class TerminalMode(str, Enum):
    ARGV = "argv"
    COMMAND_STRING = "command_string"


class TerminalCandidate(NamedTuple):
    executable: str
    exec_prefix: tuple[str, ...]
    mode: TerminalMode = TerminalMode.ARGV


class ResolvedTerminal(NamedTuple):
    executable: str
    exec_prefix: tuple[str, ...]
    mode: TerminalMode


TERMINAL_CANDIDATES: tuple[TerminalCandidate, ...] = (
    TerminalCandidate("x-terminal-emulator", ("-e",)),
    TerminalCandidate("sensible-terminal", ("-e",)),
    TerminalCandidate("gnome-terminal", ("--",)),
    TerminalCandidate("kgx", ("--",)),
    TerminalCandidate("ptyxis", ("--",)),
    TerminalCandidate("mate-terminal", ("-e",), TerminalMode.COMMAND_STRING),
    TerminalCandidate("xfce4-terminal", ("-e",), TerminalMode.COMMAND_STRING),
    TerminalCandidate("lxterminal", ("-e",), TerminalMode.COMMAND_STRING),
    TerminalCandidate("qterminal", ("-e",)),
    TerminalCandidate("konsole", ("-e",)),
    TerminalCandidate("tilix", ("-e",), TerminalMode.COMMAND_STRING),
    TerminalCandidate("terminator", ("-e",), TerminalMode.COMMAND_STRING),
    TerminalCandidate("alacritty", ("-e",)),
    TerminalCandidate("kitty", ("-e",)),
    TerminalCandidate("foot", ("-e",)),
    TerminalCandidate("ghostty", ("-e",)),
    TerminalCandidate("wezterm", ("start", "--")),
    TerminalCandidate("terminology", ("-e",), TerminalMode.COMMAND_STRING),
    TerminalCandidate("deepin-terminal", ("-e",), TerminalMode.COMMAND_STRING),
    TerminalCandidate("urxvt", ("-e",)),
    TerminalCandidate("rxvt", ("-e",)),
    TerminalCandidate("xterm", ("-e",)),
)
DESKTOP_EXEC_FIELD_CODES_RE = re.compile(r"%[uUfFdDnNickvm]")


def shell_path() -> str:
    """Return the user's shell, falling back to POSIX sh."""
    return os.environ.get("SHELL") or "/bin/sh"


def build_shell_argv(*, command: str, shell: str | None = None) -> list[str]:
    """Build argv for shell-compatible command text."""
    return [shell or shell_path(), "-lc", command.strip()]


@cache
def _flatpak_host_terminal_name() -> str | None:
    """Resolve the first host terminal in one sandbox-crossing command."""
    if flatpak.spawn_path() is None:
        return None

    names = " ".join(
        shlex.quote(candidate.executable) for candidate in TERMINAL_CANDIDATES
    )
    script = (
        f"for cmd in {names}; do "
        'command -v "$cmd" >/dev/null 2>&1 && { printf "%s" "$cmd"; exit 0; }; '
        "done; exit 1"
    )
    cmd = flatpak.host_command(["sh", "-lc", script], sanitize_env=False)
    if cmd is None:
        return None
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=TERMINAL_LOOKUP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    terminal = result.stdout.strip()
    return terminal or None


def resolve_terminal_executable(executable: str) -> str | None:
    """Resolve a terminal executable locally."""
    return shutil.which(executable)


def find_terminal(
    resolver: Callable[[str], str | None] | None = None,
) -> ResolvedTerminal | None:
    """Return the first available terminal command and its exec prefix."""
    if resolver is None:
        host_terminal = _flatpak_host_terminal_name()
        if host_terminal is not None:
            for candidate in TERMINAL_CANDIDATES:
                if candidate.executable == host_terminal:
                    return ResolvedTerminal(
                        executable=host_terminal,
                        exec_prefix=candidate.exec_prefix,
                        mode=candidate.mode,
                    )
        resolver = resolve_terminal_executable

    for candidate in TERMINAL_CANDIDATES:
        path = resolver(candidate.executable)
        if path is not None:
            return ResolvedTerminal(
                executable=path,
                exec_prefix=candidate.exec_prefix,
                mode=candidate.mode,
            )
    return None


def build_terminal_argv(
    *,
    command: str,
    shell: str | None = None,
    resolver: Callable[[str], str | None] | None = None,
) -> list[str] | None:
    """Build argv for running command in a terminal emulator."""
    terminal = find_terminal(resolver=resolver)
    if terminal is None:
        return None
    shell_argv = build_shell_argv(command=command, shell=shell)
    if terminal.mode == TerminalMode.COMMAND_STRING:
        exec_argv = [shlex.join(shell_argv)]
    else:
        exec_argv = shell_argv
    return [
        terminal.executable,
        *terminal.exec_prefix,
        *exec_argv,
    ]


def launch_command(
    *,
    command: str,
    run_in_terminal: bool,
    popen: Callable[..., object] = subprocess.Popen,
    resolver: Callable[[str], str | None] | None = None,
) -> bool:
    """Launch command text, returning whether a process was started."""
    normalized = command.strip()
    if not normalized:
        return False

    argv = (
        build_terminal_argv(command=normalized, resolver=resolver)
        if run_in_terminal
        else build_shell_argv(command=normalized)
    )
    if argv is None:
        log.bind(action="launch_command").warning("No terminal emulator found")
        return False
    argv = flatpak.host_command(argv) or argv

    try:
        popen(
            argv,
            shell=False,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.bind(action="launch_command").warning(
            "Failed to launch command %s: %s",
            normalized,
            exc,
        )
        return False
    return True


def append_file_argument(*, command: str, path: str) -> str:
    """Append a shell-quoted file path to existing command text."""
    quoted = shlex.quote(path)
    normalized = command.strip()
    return f"{normalized} {quoted}" if normalized else quoted


def clean_desktop_exec(command: str) -> str:
    """Remove freedesktop Exec field placeholders from a command line."""
    return DESKTOP_EXEC_FIELD_CODES_RE.sub("", command).strip()
