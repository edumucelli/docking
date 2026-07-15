# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Command, history, and application matching helpers for Run Application."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable, Iterable
from enum import Enum
from functools import cache
from typing import NamedTuple, Protocol

from gi.repository import Gio, GLib

from docking.applets.runcommand import meta
from docking.log import get_logger, with_context
from docking.platform.environment import flatpak

log = with_context(get_logger(name="runcommand"), applet_id=meta.id)

HISTORY_LIMIT = 20
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


class ApplicationLike(Protocol):
    """Application entry consumed by the Run Application dialog."""

    @property
    def app_info(self) -> Gio.DesktopAppInfo | None: ...

    def get_display_name(self) -> str: ...

    def get_icon(self) -> Gio.Icon | None: ...

    def launch(
        self,
        files: list[Gio.File] | None,
        context: Gio.AppLaunchContext | None,
    ) -> None: ...


def normalize_history(raw_history: object) -> list[str]:
    """Return non-empty, de-duplicated history entries."""
    if not isinstance(raw_history, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in raw_history:
        if not isinstance(value, str):
            continue
        command = value.strip()
        if not command or command in seen:
            continue
        result.append(command)
        seen.add(command)
        if len(result) >= HISTORY_LIMIT:
            break
    return result


def updated_history(
    *,
    history: Iterable[str],
    command: str,
    limit: int = HISTORY_LIMIT,
) -> list[str]:
    """Add command at the front while preserving a bounded unique history."""
    normalized = command.strip()
    if not normalized:
        return list(history)[:limit]
    result = [normalized]
    result.extend(item for item in history if item != normalized)
    return result[:limit]


def shell_path() -> str:
    """Return the user's shell, falling back to POSIX sh."""
    return os.environ.get("SHELL") or "/bin/sh"


def build_shell_argv(*, command: str, shell: str | None = None) -> list[str]:
    """Build argv for shell-compatible Alt+F2 command text."""
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


def app_display_name(app: ApplicationLike) -> str:
    return (app.get_display_name() or "").strip()


def app_command_text(app: ApplicationLike) -> str:
    """Return command text suitable for the dialog entry."""
    app_info = app.app_info
    commandline = ""
    if app_info is not None:
        commandline = clean_desktop_exec(str(app_info.get_commandline() or ""))
    return commandline or app_display_name(app)


def app_description(app: ApplicationLike) -> str:
    """Return the best available app description/comment."""
    app_info = app.app_info
    if app_info is None:
        return app_display_name(app)
    for text in (
        str(app_info.get_description() or "").strip(),
        _optional_app_info_text(app_info, "get_comment"),
        _optional_app_info_text(app_info, "get_generic_name"),
    ):
        if text:
            return text
    return app_display_name(app)


def _optional_app_info_text(app_info: Gio.DesktopAppInfo, getter_name: str) -> str:
    """Read metadata that is not exposed by every PyGObject runtime.

    ``Gio.DesktopAppInfo`` consistently provides a description, while comment
    and generic-name accessors vary across the available bindings. Keep those
    two fields as best-effort metadata rather than requiring them at runtime.
    """
    getter = getattr(app_info, getter_name, None)
    if not callable(getter):
        return ""
    return str(getter() or "").strip()


def match_application(
    *,
    apps: Iterable[ApplicationLike],
    text: str,
) -> ApplicationLike | None:
    """Match typed text to an application by exact name, then unique prefix."""
    needle = text.strip().casefold()
    if not needle:
        return None

    app_list = list(apps)
    exact = [app for app in app_list if app_display_name(app).casefold() == needle]
    if len(exact) == 1:
        return exact[0]

    prefix = [
        app for app in app_list if app_display_name(app).casefold().startswith(needle)
    ]
    if len(prefix) == 1:
        return prefix[0]
    return None


def launch_application(app: ApplicationLike) -> bool:
    """Launch a matched application through its existing adapter."""
    try:
        app.launch([], None)
    except (GLib.Error, OSError) as exc:
        log.bind(action="launch_application", app=app_display_name(app)).warning(
            "Failed to launch application: %s",
            exc,
        )
        return False
    return True


def prefs_payload(*, history: Iterable[str]) -> dict[str, list[str]]:
    return {"history": list(history)[:HISTORY_LIMIT]}
