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

"""Persistent greeting state and New Year trigger rules.

This module keeps one tiny piece of process-independent state:

- whether Docking has ever completed one successful launch,
- which calendar year last received the New Year greeting.

The behavior intentionally mirrors Cairo-Dock's startup greeting policy:

- no greeting on the very first successful launch,
- greeting only during the first half of January,
- greeting at most once per calendar year.

That state is not user configuration. It is runtime bookkeeping, so it lives in
XDG state storage rather than in ``dock.json``.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from docking.core.paths import ensure_parent_dir
from docking.log import get_logger
from docking.platform.environment import docking_state_dir

logger = get_logger("greeting")

DEFAULT_STATE_DIR = docking_state_dir()
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "startup.json"
NEW_YEAR_GREETING_LAST_DAY = 15


@dataclass(frozen=True, slots=True)
class StartupGreetingState:
    """Persistent state used to decide whether a greeting is due."""

    completed_first_launch: bool = False
    last_year_greeted: int = 0


def load_state(path: Path | str | None = None) -> StartupGreetingState:
    """Load greeting state, falling back to defaults on missing/corrupt data."""
    state_path = Path(path) if path else DEFAULT_STATE_FILE
    if not state_path.exists():
        return StartupGreetingState()

    try:
        with state_path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception as exc:
        logger.warning("Failed to load greeting state %s: %s", state_path, exc)
        return StartupGreetingState()

    if not isinstance(raw, dict):
        logger.warning(
            "Invalid greeting state payload in %s; using defaults",
            state_path,
        )
        return StartupGreetingState()

    return StartupGreetingState(
        completed_first_launch=_coerce_bool(
            raw.get("completed_first_launch"),
            default=False,
        ),
        last_year_greeted=_coerce_int(
            raw.get("last_year_greeted"),
            default=0,
        ),
    )


def save_state(
    state: StartupGreetingState,
    *,
    path: Path | str | None = None,
) -> None:
    """Persist greeting state atomically."""
    state_path = Path(path) if path else DEFAULT_STATE_FILE
    ensure_parent_dir(state_path)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=state_path.parent,
        prefix=f".{state_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)
        json.dump(
            {
                "completed_first_launch": state.completed_first_launch,
                "last_year_greeted": state.last_year_greeted,
            },
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")

    tmp_path.replace(state_path)


def consume_new_year_greeting(
    *,
    path: Path | str | None = None,
    now: datetime | None = None,
) -> int | None:
    """Mark one successful launch and return the year to greet, if any.

    Returns the current year when the greeting should be shown on this launch,
    otherwise *None*.
    """
    current = now or datetime.now()
    state = load_state(path=path)

    show_year: int | None = None
    current_year = current.year

    if (
        state.completed_first_launch
        and current.month == 1
        and current.day <= NEW_YEAR_GREETING_LAST_DAY
        and state.last_year_greeted < current_year
    ):
        show_year = current_year

    next_state = StartupGreetingState(
        completed_first_launch=True,
        last_year_greeted=(
            show_year if show_year is not None else state.last_year_greeted
        ),
    )
    save_state(next_state, path=path)
    return show_year


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int | float):
            return int(value)
        if isinstance(value, str):
            return int(value.strip())
    except (TypeError, ValueError):
        return default
    return default
