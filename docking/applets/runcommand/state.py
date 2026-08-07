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

from collections.abc import Iterable

from docking.platform import commands as _commands
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.listing import (
    ApplicationListing,
    listing_description,
    listing_desktop_id,
    listing_exec_line,
    listing_generic_name,
    listing_key,
    listing_name,
)
from docking.platform.applications.registry import UnidentifiedApplicationListing
from docking.platform.applications.types import ApplicationInfo

HISTORY_LIMIT = 20

# Compatibility aliases for the generic command API now owned by the platform
# layer. Keep these as direct aliases so callers share the same constants,
# caches, and injection seams.
TERMINAL_LOOKUP_TIMEOUT_SECONDS = _commands.TERMINAL_LOOKUP_TIMEOUT_SECONDS
TerminalMode = _commands.TerminalMode
TerminalCandidate = _commands.TerminalCandidate
ResolvedTerminal = _commands.ResolvedTerminal
TERMINAL_CANDIDATES = _commands.TERMINAL_CANDIDATES
DESKTOP_EXEC_FIELD_CODES_RE = _commands.DESKTOP_EXEC_FIELD_CODES_RE
shell_path = _commands.shell_path
build_shell_argv = _commands.build_shell_argv
_flatpak_host_terminal_name = _commands._flatpak_host_terminal_name
resolve_terminal_executable = _commands.resolve_terminal_executable
find_terminal = _commands.find_terminal
build_terminal_argv = _commands.build_terminal_argv
launch_command = _commands.launch_command
append_file_argument = _commands.append_file_argument
clean_desktop_exec = _commands.clean_desktop_exec


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


def app_display_name(app: ApplicationListing) -> str:
    return listing_name(app).strip()


def app_command_text(app: ApplicationListing) -> str:
    """Return command text suitable for the dialog entry."""
    if isinstance(app, UnidentifiedApplicationListing) or (
        isinstance(app, ApplicationInfo) and app.has_gio_source
    ):
        commandline = clean_desktop_exec(listing_exec_line(app))
        if commandline:
            return commandline
    return app_display_name(app)


def app_description(app: ApplicationListing) -> str:
    """Return the best available app description/comment."""
    if isinstance(app, UnidentifiedApplicationListing) or (
        isinstance(app, ApplicationInfo) and app.has_gio_source
    ):
        for text in (
            listing_description(app).strip(),
            listing_generic_name(app).strip(),
        ):
            if text:
                return text
    return app_display_name(app)


def match_application(
    *,
    apps: Iterable[ApplicationListing],
    text: str,
) -> ApplicationListing | None:
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


def launch_application(
    *,
    app: ApplicationListing,
    launcher: ApplicationLauncher | None,
) -> bool:
    """Launch a matched canonical or ID-less registry listing."""
    if launcher is None:
        return False
    desktop_id = listing_desktop_id(app)
    if desktop_id is not None:
        return launcher.launch(desktop_id)
    if isinstance(app, UnidentifiedApplicationListing):
        opaque_key = listing_key(app)
        return opaque_key is not None and launcher.launch_listing(opaque_key)
    return False


def prefs_payload(*, history: Iterable[str]) -> dict[str, list[str]]:
    return {"history": list(history)[:HISTORY_LIMIT]}
