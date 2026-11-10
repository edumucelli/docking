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

"""Claude Code usage backend."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, cast

from docking.applets.aiusage.backends.base import (
    ProviderSessions,
    event_date,
    transcript_entries,
)
from docking.applets.aiusage.state import (
    ModelUsage,
    Provider,
    _has_usage,
    _nonnegative_count,
)
from docking.applets.aiusage.store import replace_session
from docking.core.paths import ensure_parent_dir
from docking.log import get_logger, with_context
from docking.platform.composition.selection import platform_provider

log = with_context(get_logger(name="aiusage.claude"))

_CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"


class ClaudeBackend:
    provider = Provider.CLAUDE

    def register_hooks(self) -> None:
        register_hooks()

    def poll_today(self) -> ProviderSessions:
        return query_today()

    def handle_hook(self, *, event: str, payload: object) -> None:
        if event != "Stop" or not isinstance(payload, dict):
            return
        payload_data = cast("dict[str, Any]", payload)
        transcript_path = payload_data.get("transcript_path")
        if not transcript_path:
            return
        session_id = payload_data.get("session_id") or Path(transcript_path).stem
        day = _today_iso()
        model_usage = parse_transcript(path=Path(transcript_path), date=day)
        if model_usage:
            replace_session(
                session_id=str(session_id), model_usage=model_usage, date=day
            )


def register_hooks() -> None:
    """Ensure Claude Code hooks point to our CLI entry point."""
    try:
        if _CLAUDE_SETTINGS.exists():
            settings = json.loads(_CLAUDE_SETTINGS.read_text(encoding="utf-8"))
        else:
            settings = {}
    except (OSError, json.JSONDecodeError):
        log.bind(action="register_hooks").warning("Could not read %s", _CLAUDE_SETTINGS)
        return

    hooks = settings.setdefault("hooks", {})
    if not platform_provider().usage_hooks().update_claude(hooks):
        return
    try:
        ensure_parent_dir(_CLAUDE_SETTINGS)
        _CLAUDE_SETTINGS.write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        log.bind(action="register_hooks").warning(
            "Could not write %s", _CLAUDE_SETTINGS
        )


def query_today() -> ProviderSessions:
    """Read today's Claude transcripts, including sessions not seen by hooks."""
    root = Path.home() / ".claude"
    if not root.is_dir():
        return {}

    day = _today_iso()
    today_start = datetime.datetime.fromisoformat(day).timestamp()
    result: ProviderSessions = {}
    for path in root.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < today_start:
                continue
        except OSError as exc:
            log.debug("Failed to stat Claude transcript %s: %s", path, exc)
            continue

        model_usage = parse_transcript(path=path, date=day)
        if model_usage:
            result[path.stem] = model_usage
    return result


def parse_transcript(path: Path, *, date: str | None = None) -> dict[str, ModelUsage]:
    """Accumulate only usage produced on the requested local calendar day."""
    result: dict[str, ModelUsage] = {}
    day = date or _today_iso()
    try:
        for entry in transcript_entries(path):
            if (
                entry.get("type") != "assistant"
                or event_date(entry.get("timestamp"), fallback=day) != day
            ):
                continue
            msg = entry.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            model = msg.get("model")
            if not isinstance(usage, dict) or not isinstance(model, str) or not model:
                continue
            prev = result.get(model, ModelUsage())
            result[model] = ModelUsage(
                input_tokens=prev.input_tokens
                + _nonnegative_count(usage.get("input_tokens")),
                output_tokens=prev.output_tokens
                + _nonnegative_count(usage.get("output_tokens")),
                cache_write_tokens=prev.cache_write_tokens
                + _nonnegative_count(usage.get("cache_creation_input_tokens")),
                cache_read_tokens=prev.cache_read_tokens
                + _nonnegative_count(usage.get("cache_read_input_tokens")),
            )
    except (OSError, UnicodeError) as exc:
        log.debug("Failed to read Claude transcript %s: %s", path, exc)
        return {}
    return {m: u for m, u in result.items() if _has_usage(u)}


def _today_iso() -> str:
    return datetime.date.today().isoformat()
