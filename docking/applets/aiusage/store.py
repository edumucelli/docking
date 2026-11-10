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

"""Persistent storage helpers for AI usage hooks."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from docking.applets.aiusage import state as aiusage_state
from docking.core.paths import ensure_parent_dir
from docking.log import get_logger
from docking.platform.environment import docking_config_dir, docking_state_dir
from docking.platform.host.selection import host_services

PREFS_KEY = "aiusage"
PREFS_KEY_LEGACY = "claude"

log = get_logger("aiusage.store")


def config_path() -> Path:
    return docking_config_dir() / "dock.json"


def usage_path() -> Path:
    return docking_state_dir() / "aiusage.json"


def read_prefs_from_disk() -> dict[str, Any] | None:
    """Read usage state, falling back to old preferences only before migration."""
    path = usage_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _legacy_prefs()
    except (OSError, UnicodeError, ValueError) as exc:
        raise OSError("Cannot read saved AI usage history") from exc
    if not isinstance(document, dict) or not isinstance(document.get("days"), list):
        raise OSError("Invalid saved AI usage history")
    return document


def _legacy_prefs() -> dict[str, Any] | None:
    original = config_path()
    path = original.resolve()
    candidates = (path, path.with_name(path.name + ".bak"))
    if path != original:
        candidates += (original.with_name(original.name + ".bak"),)
    for candidate in candidates:
        try:
            document = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        prefs = document.get("applet_prefs")
        if not isinstance(prefs, dict):
            continue
        value = prefs.get(PREFS_KEY) or prefs.get(PREFS_KEY_LEGACY)
        if isinstance(value, dict):
            return value
    return None


def _update_state(
    update: Callable[[aiusage_state.AiUsageState], aiusage_state.AiUsageState],
) -> aiusage_state.AiUsageState:
    path = ensure_parent_dir(usage_path())
    lock_path = path.with_name(path.name + ".lock")
    # Atomic replacement changes the data file's inode; lock a stable sibling.
    with (
        lock_path.open("a+b") as lock_file,
        host_services().writer_lock(lock_file.fileno(), path=lock_path),
    ):
        current = aiusage_state.state_from_prefs(prefs=read_prefs_from_disk())
        updated = update(current)
        if updated == current and path.exists():
            return updated
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(aiusage_state.prefs_from_state(updated), stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
            host_services().flush_directory(path.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return updated


def replace_sessions(
    sessions: dict[str, dict[str, aiusage_state.ModelUsage]],
    *,
    date: str | None = None,
) -> aiusage_state.AiUsageState:
    def update(current: aiusage_state.AiUsageState) -> aiusage_state.AiUsageState:
        for session_id, model_usage in sessions.items():
            current = aiusage_state.set_session(
                state=current, session_id=session_id, model_usage=model_usage, date=date
            )
        return current

    return _update_state(update)


def replace_session(
    *,
    session_id: str,
    model_usage: dict[str, aiusage_state.ModelUsage],
    date: str | None = None,
) -> None:
    if model_usage:
        replace_sessions({session_id: model_usage}, date=date)


def reset_usage_today() -> aiusage_state.AiUsageState:
    return _update_state(aiusage_state.reset_today)
