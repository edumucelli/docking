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

"""OpenCode usage backend."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from docking.applets.aiusage.backends.base import ProviderSessions
from docking.applets.aiusage.state import (
    OPENCODE_PREFIX,
    ModelUsage,
    Provider,
    _nonnegative_cost,
    _nonnegative_count,
)
from docking.log import get_logger

log = get_logger("aiusage.opencode")

_OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


class OpenCodeBackend:
    provider = Provider.OPENCODE

    def register_hooks(self) -> None:
        return

    def poll_today(self) -> ProviderSessions:
        return query_today()

    def handle_hook(self, *, event: str, payload: object) -> None:
        _ = event, payload


def query_today() -> ProviderSessions:
    """Query OpenCode SQLite for today's sessions."""
    import sqlite3

    if not _OPENCODE_DB.exists():
        return {}

    today = datetime.datetime.fromisoformat(_today_iso())
    today_start_ms = int(today.timestamp() * 1000)
    tomorrow_start_ms = int((today + datetime.timedelta(days=1)).timestamp() * 1000)

    try:
        conn = sqlite3.connect(_OPENCODE_DB.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        log.debug("Failed to open OpenCode database %s: %s", _OPENCODE_DB, exc)
        return {}

    try:
        rows = conn.execute(
            "SELECT session_id, data FROM message "
            "WHERE time_created >= ? AND time_created < ?",
            (today_start_ms, tomorrow_start_ms),
        )
        result: ProviderSessions = {}
        for sid, data_str in rows:
            try:
                data = json.loads(data_str)
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict) or not isinstance(sid, str):
                continue
            model_id = data.get("modelID")
            tokens = data.get("tokens")
            if (
                not isinstance(model_id, str)
                or not model_id
                or not isinstance(tokens, dict)
            ):
                continue
            cost = _nonnegative_cost(data.get("cost"))
            inp = _nonnegative_count(tokens.get("input"))
            out = _nonnegative_count(tokens.get("output"))
            if not (inp or out or cost):
                continue
            by_model = result.setdefault(f"oc:{sid}", {})
            key = f"{OPENCODE_PREFIX}{model_id}"
            prev = by_model.get(key, ModelUsage())
            by_model[key] = ModelUsage(
                input_tokens=prev.input_tokens + inp,
                output_tokens=prev.output_tokens + out,
                precalculated_cost=prev.precalculated_cost + cost,
            )
        return result
    finally:
        conn.close()


def _today_iso() -> str:
    return datetime.date.today().isoformat()
