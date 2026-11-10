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
import math
import re
import sys
from functools import lru_cache
from pathlib import Path

from docking.applets.aiusage.backends.base import (
    ProviderSessions,
    event_date,
    transcript_entries,
)
from docking.applets.aiusage.state import ModelUsage, Provider
from docking.applets.aiusage.store import replace_session
from docking.log import get_logger, with_context
from docking.platform.composition.selection import platform_provider
from docking.platform.host.selection import host_services

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
    our_toml = "notify = " + json.dumps(
        host_services().hook_argv(root, sys.executable, "codex"), ensure_ascii=False
    )

    notify_match = re.search(
        r"""^[ \t]*(?:notify|"notify"|'notify')\s*=\s*\["""
        r"""(?:"(?:\\.|[^"\\])*"|'[^']*'|[^\]"'])*\]""",
        content,
        re.MULTILINE,
    )
    if notify_match is not None and re.search(
        r"^[ \t]*\[", content[: notify_match.start()], re.MULTILINE
    ):
        notify_match = None
    if notify_match:
        existing = notify_match.group(0)
        if (
            platform_provider()
            .usage_hooks()
            .codex_notify_is_current(existing, our_toml)
        ):
            return
        normalized = existing.replace("\\\\", "/").replace("\\", "/").casefold()
        if not any(
            marker in normalized
            for marker in (
                "docking.applets.aiusage.hook",
                "docking.applets.claude.hook",
                "/docking/applets/aiusage/hook_runner.py",
                "/docking-hook.exe",
            )
        ):
            log.bind(action="register_codex_hook").info(
                "Codex notify already set; preserving the existing handler"
            )
            return
        content = (
            content[: notify_match.start()] + our_toml + content[notify_match.end() :]
        )
    else:
        section_match = re.search(r"^[ \t]*\[", content, re.MULTILINE)
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

    if not isinstance(data, dict):
        return
    thread_id = data.get("thread-id")
    session_path = find_session(thread_id=thread_id)
    if not session_path:
        return
    session_id = thread_id or session_path.stem
    day = _today_iso()
    model_usage = parse_transcript(path=session_path, date=day)
    if model_usage:
        replace_session(session_id=session_id, model_usage=model_usage, date=day)


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


_USAGE_COUNTER_KEYS = ("input_tokens", "output_tokens", "cached_input_tokens")


def _token_count(value: object) -> int:
    """Return a usable token count, or zero.

    A JSON number can be non-finite (``1e309`` parses as ``inf``) and ``int()``
    raises OverflowError on that, which must not abort the transcript.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    if not math.isfinite(value):
        return 0
    return int(value)


def parse_transcript(path: Path, *, date: str | None = None) -> dict[str, ModelUsage]:
    return _read_transcript(path, date or _today_iso())[1]


def _read_transcript(path: Path, date: str) -> tuple[str, dict[str, ModelUsage]]:
    try:
        stamp = path.stat()
        return _parse_transcript(path, stamp.st_mtime_ns, stamp.st_size, date)
    except (OSError, UnicodeError) as exc:
        log.debug("Failed to read Codex transcript %s: %s", path, exc)
        return path.stem, {}


@lru_cache(maxsize=128)
def _parse_transcript(
    path: Path, _mtime_ns: int, _size: int, date: str
) -> tuple[str, dict[str, ModelUsage]]:
    model = ""
    identifier = path.stem
    best_total = 0
    previous = dict.fromkeys(_USAGE_COUNTER_KEYS, 0)
    result: dict[str, ModelUsage] = {}
    for entry in transcript_entries(path):
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        if entry.get("type") == "session_meta":
            value = payload.get("id")
            if isinstance(value, str) and value:
                identifier = value
        elif entry.get("type") == "turn_context":
            if "model" in payload:
                value = payload["model"]
                model = value if isinstance(value, str) else ""
        elif entry.get("type") == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            counters = info.get("total_token_usage")
            if not isinstance(counters, dict):
                continue
            total = _token_count(counters.get("total_tokens"))
            if total <= best_total:
                continue
            current = {
                key: max(0, _token_count(counters.get(key)))
                for key in _USAGE_COUNTER_KEYS
            }
            delta = {
                key: max(0, current[key] - previous[key]) for key in _USAGE_COUNTER_KEYS
            }
            previous, best_total = current, total
            if not model or event_date(entry.get("timestamp"), fallback=date) != date:
                continue
            old = result.get(model, ModelUsage())
            result[model] = ModelUsage(
                input_tokens=old.input_tokens + delta["input_tokens"],
                output_tokens=old.output_tokens + delta["output_tokens"],
                cache_read_tokens=old.cache_read_tokens + delta["cached_input_tokens"],
            )
    return identifier, result


def session_id(path: Path) -> str:
    return _read_transcript(path, _today_iso())[0]


def query_today() -> ProviderSessions:
    """Read today's Codex sessions from local JSONL transcripts."""
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.is_dir():
        return {}

    day = _today_iso()
    today_start = datetime.datetime.fromisoformat(day).timestamp()
    result: ProviderSessions = {}
    for jsonl in sessions_dir.rglob("*.jsonl"):
        try:
            if jsonl.stat().st_mtime < today_start:
                continue
        except OSError as exc:
            log.debug("Failed to stat Codex transcript %s: %s", jsonl, exc)
            continue

        identifier, model_usage = _read_transcript(jsonl, day)
        if model_usage:
            result[identifier] = model_usage
    return result


def _today_iso() -> str:
    return datetime.date.today().isoformat()
