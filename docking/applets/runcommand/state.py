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
    activate_listing,
)
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationListing,
    TransientApplicationInfo,
)

HISTORY_LIMIT = 20


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
    return app.name.strip()


def app_command_text(app: ApplicationListing) -> str:
    """Return command text suitable for the dialog entry."""
    if isinstance(app, TransientApplicationInfo) or (
        isinstance(app, ApplicationInfo) and app.has_gio_source
    ):
        commandline = _commands.clean_desktop_exec(app.exec_line)
        if commandline:
            return commandline
    return app_display_name(app)


def app_description(app: ApplicationListing) -> str:
    """Return the best available app description/comment."""
    if isinstance(app, TransientApplicationInfo) or (
        isinstance(app, ApplicationInfo) and app.has_gio_source
    ):
        for text in (
            app.description.strip(),
            app.generic_name.strip(),
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
    launcher: ApplicationLauncher,
) -> bool:
    """Launch a matched canonical or ID-less registry listing."""
    return activate_listing(launcher, app)


def prefs_payload(*, history: Iterable[str]) -> dict[str, list[str]]:
    return {"history": list(history)[:HISTORY_LIMIT]}
