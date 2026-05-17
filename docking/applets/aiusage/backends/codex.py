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

"""Codex CLI usage backend."""

from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

from docking.applets.aiusage.backends.base import ProviderSessions
from docking.applets.aiusage.state import ModelUsage, Provider
from docking.applets.aiusage.store import replace_session
from docking.log import get_logger, with_context

log = with_context(get_logger(name="aiusage.codex"))

_CODEX_CONFIG = Path.home() / ".codex" / "config.toml"


class CodexBackend:
    provider = Provider.CODEX

    def register_hooks(self) -> None:
        register_hook()

    def poll_today(self) -> ProviderSessions:
        return query_today()

    def handle_hook(self, *, event: str, payload: object) -> None:
        _ = event
        if isinstance(payload, str):
            handle_turn(json_arg=payload)


def register_hook() -> None:
    """Ensure Codex CLI notify points to our hook."""
    try:
        if _CODEX_CONFIG.exists():
            content = _CODEX_CONFIG.read_text(encoding="utf-8")
        else:
            return
    except OSError as exc:
        log.debug("Failed to read Codex config %s: %s", _CODEX_CONFIG, exc)
        return

    root = Path(__file__).resolve().parents[4]
    our_toml = (
        f'notify = ["env", "PYTHONPATH={root}",'
        f' "{sys.executable}", "-m", "docking.applets.aiusage.hook", "codex"]'
    )

    notify_match = re.search(r"^notify\s*=\s*\[.*?\]", content, re.MULTILINE)
    if notify_match:
        existing = notify_match.group(0)
        if "PYTHONPATH" in existing and "docking.applets.aiusage.hook" in existing:
            return
        if "codex-sync" in existing:
            log.bind(action="register_codex_hook").info(
                "Codex notify already set to codex-sync, not overwriting"
            )
            return
        content = (
            content[: notify_match.start()] + our_toml + content[notify_match.end() :]
        )
    else:
        section_match = re.search(r"^\[", content, re.MULTILINE)
        if section_match:
            content = (
                content[: section_match.start()]
                + our_toml
                + "\n"
                + content[section_match.start() :]
            )
        else:
            content = content.rstrip() + "\n" + our_toml + "\n"

    try:
        _CODEX_CONFIG.write_text(content, encoding="utf-8")
    except OSError:
        log.bind(action="register_codex_hook").warning(
            "Could not write %s",
            _CODEX_CONFIG,
        )


def handle_turn(json_arg: str) -> None:
    try:
        data = json.loads(json_arg)
    except (json.JSONDecodeError, ValueError) as exc:
        log.debug("Failed to parse Codex hook payload %r: %s", json_arg, exc)
        data = {}

    thread_id = data.get("thread-id")
    session_path = find_session(thread_id=thread_id)
    if not session_path:
        return
    session_id = thread_id or session_path.stem
    model_usage = parse_transcript(path=session_path)
    if model_usage:
        replace_session(session_id=session_id, model_usage=model_usage)


def find_session(thread_id: str | None = None) -> Path | None:
    """Find a Codex session JSONL file by thread-id or most recent."""
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.is_dir():
        return None

    if thread_id:
        for jsonl in sorted(sessions_dir.rglob("*.jsonl"), reverse=True):
            if thread_id in jsonl.name:
                return jsonl

    candidates = sorted(
        sessions_dir.rglob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def parse_transcript(path: Path) -> dict[str, ModelUsage]:
    """Read a Codex CLI JSONL session and extract cumulative usage."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.debug("Failed to read Codex transcript %s: %s", path, exc)
        return {}

    model: str = ""
    best_total: int = 0
    best_usage: dict[str, int] = {}

    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            log.debug("Failed to parse Codex transcript line in %s: %s", path, exc)
            continue

        entry_type = entry.get("type", "")
        if entry_type == "turn_context":
            m = entry.get("payload", {}).get("model", "")
            if m:
                model = m

        if entry_type == "event_msg":
            payload = entry.get("payload", {})
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            total_usage = info.get("total_token_usage", {})
            total = int(total_usage.get("total_tokens", 0))
            if total > best_total:
                best_total = total
                best_usage = total_usage

    if not model or not best_usage:
        return {}

    return {
        model: ModelUsage(
            input_tokens=int(best_usage.get("input_tokens", 0)),
            output_tokens=int(best_usage.get("output_tokens", 0)),
            cache_write_tokens=0,
            cache_read_tokens=int(best_usage.get("cached_input_tokens", 0)),
        )
    }


def session_id(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if entry.get("type") != "session_meta":
                continue
            sid = entry.get("payload", {}).get("id", "")
            if sid:
                return str(sid)
    except OSError as exc:
        log.debug("Failed to read Codex session metadata %s: %s", path, exc)
    return path.stem


def query_today() -> ProviderSessions:
    """Read today's Codex sessions from local JSONL transcripts."""
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.is_dir():
        return {}

    today_start = datetime.datetime.fromisoformat(_today_iso()).timestamp()
    result: ProviderSessions = {}
    for jsonl in sessions_dir.rglob("*.jsonl"):
        try:
            if jsonl.stat().st_mtime < today_start:
                continue
        except OSError as exc:
            log.debug("Failed to stat Codex transcript %s: %s", jsonl, exc)
            continue

        model_usage = parse_transcript(path=jsonl)
        if model_usage:
            result[session_id(path=jsonl)] = model_usage
    return result


def _today_iso() -> str:
    return datetime.date.today().isoformat()
