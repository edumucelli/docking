"""OpenCode usage backend."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from docking.applets.aiusage.backends.base import ProviderSessions
from docking.applets.aiusage.state import OPENCODE_PREFIX, ModelUsage, Provider
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

    today_start_ms = int(
        datetime.datetime.fromisoformat(_today_iso()).timestamp() * 1000
    )

    try:
        conn = sqlite3.connect(f"file:{_OPENCODE_DB}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        log.debug("Failed to open OpenCode database %s: %s", _OPENCODE_DB, exc)
        return {}

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM session WHERE time_created >= ?",
            (today_start_ms,),
        )
        session_ids = [r[0] for r in cur.fetchall()]
        if not session_ids:
            return {}

        result: ProviderSessions = {}
        for sid in session_ids:
            cur.execute("SELECT data FROM message WHERE session_id = ?", (sid,))
            by_model: dict[str, ModelUsage] = {}
            for (data_str,) in cur.fetchall():
                try:
                    data = json.loads(data_str)
                except (json.JSONDecodeError, ValueError) as exc:
                    log.debug(
                        "Failed to parse OpenCode message row for session %s: %s",
                        sid,
                        exc,
                    )
                    continue
                model_id = data.get("modelID", "")
                if not model_id:
                    continue
                tokens = data.get("tokens", {})
                cost = float(data.get("cost", 0) or 0)
                inp = int(tokens.get("input", 0))
                out = int(tokens.get("output", 0))
                if not (inp or out or cost):
                    continue

                key = f"{OPENCODE_PREFIX}{model_id}"
                prev = by_model.get(key, ModelUsage())
                by_model[key] = ModelUsage(
                    input_tokens=prev.input_tokens + inp,
                    output_tokens=prev.output_tokens + out,
                    precalculated_cost=prev.precalculated_cost + cost,
                )
            if by_model:
                result[f"oc:{sid}"] = by_model
        return result
    finally:
        conn.close()


def _today_iso() -> str:
    return datetime.date.today().isoformat()
