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
import sys
from pathlib import Path
from typing import Any, cast

from docking.applets.aiusage.backends.base import ProviderSessions
from docking.applets.aiusage.state import ModelUsage, Provider, _has_usage
from docking.applets.aiusage.store import replace_session
from docking.log import get_logger, with_context

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
        model_usage = parse_transcript(path=Path(transcript_path))
        if model_usage:
            replace_session(session_id=str(session_id), model_usage=model_usage)


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
    changed = False
    prefix = _hook_command_prefix()

    # Remove stale module paths from before the aiusage applet was generalized.
    for event_key in ("Stop", "SessionStart"):
        entries = hooks.get(event_key, [])
        cleaned = [
            e
            for e in entries
            if not any(
                "docking.applets.claude.hook" in h.get("command", "")
                for h in e.get("hooks", [])
            )
        ]
        if len(cleaned) != len(entries):
            hooks[event_key] = cleaned
            changed = True

    stop_entries = hooks.get("Stop", [])
    if not _has_hook(entries=stop_entries, needle=prefix):
        stop_entries.append(
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"{prefix} claude Stop"}],
            }
        )
        hooks["Stop"] = stop_entries
        changed = True

    start_entries = hooks.get("SessionStart", [])
    if not _has_hook(entries=start_entries, needle=prefix):
        start_entries.append(
            {
                "hooks": [
                    {"type": "command", "command": f"{prefix} claude SessionStart"}
                ],
            }
        )
        hooks["SessionStart"] = start_entries
        changed = True

    if changed:
        try:
            _CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            _CLAUDE_SETTINGS.write_text(
                json.dumps(settings, indent=2) + "\n",
                encoding="utf-8",
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

    today_start = datetime.datetime.fromisoformat(_today_iso()).timestamp()
    result: ProviderSessions = {}
    for path in root.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < today_start:
                continue
        except OSError as exc:
            log.debug("Failed to stat Claude transcript %s: %s", path, exc)
            continue

        model_usage = parse_transcript(path=path)
        if model_usage:
            result[path.stem] = model_usage
    return result


def parse_transcript(path: Path) -> dict[str, ModelUsage]:
    """Read a Claude Code JSONL transcript and accumulate per-model usage."""
    result: dict[str, ModelUsage] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.debug("Failed to read Claude transcript %s: %s", path, exc)
        return result

    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            log.debug("Failed to parse Claude transcript line in %s: %s", path, exc)
            continue
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        model = msg.get("model", "")
        if not model:
            continue

        prev = result.get(model, ModelUsage())
        result[model] = ModelUsage(
            input_tokens=prev.input_tokens + int(usage.get("input_tokens", 0)),
            output_tokens=prev.output_tokens + int(usage.get("output_tokens", 0)),
            cache_write_tokens=prev.cache_write_tokens
            + int(usage.get("cache_creation_input_tokens", 0)),
            cache_read_tokens=prev.cache_read_tokens
            + int(usage.get("cache_read_input_tokens", 0)),
        )
    return {m: u for m, u in result.items() if _has_usage(u)}


def _hook_command_prefix() -> str:
    root = Path(__file__).resolve().parents[4]
    return f"PYTHONPATH={root} {sys.executable} -m docking.applets.aiusage.hook"


def _has_hook(entries: list[dict[str, Any]], needle: str) -> bool:
    for entry in entries:
        for hook in entry.get("hooks", []):
            if needle in hook.get("command", ""):
                return True
    return False


def _today_iso() -> str:
    return datetime.date.today().isoformat()
