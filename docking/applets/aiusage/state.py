"""Pure state and cost logic for AI usage tracker applet."""

from __future__ import annotations

import datetime
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from docking.i18n import _

MAX_DAYS = 7


class Provider(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"


# ---------------------------------------------------------------------------
# Pricing tables
# ---------------------------------------------------------------------------

# Claude: input, output, cache_write (1.25x), cache_read (0.1x).
# Ordered most-specific first so versioned matches win.
_OPUS46 = {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50}
_OPUS4 = {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50}

CLAUDE_PRICING: tuple[tuple[str, dict[str, float]], ...] = (
    ("opus-4-5", _OPUS46),
    ("opus-4-6", _OPUS46),
    ("opus-4-1", _OPUS4),
    ("opus-4", _OPUS4),
    ("sonnet", {"input": 3, "output": 15, "cache_write": 3.75, "cache_read": 0.30}),
    ("haiku-4-5", {"input": 1, "output": 5, "cache_write": 1.25, "cache_read": 0.10}),
    ("haiku", {"input": 0.80, "output": 4, "cache_write": 1.0, "cache_read": 0.08}),
)

# Codex/OpenAI: input, cached (subtracted from input), output.
CODEX_PRICING: tuple[tuple[str, dict[str, float]], ...] = (
    ("gpt-5-pro", {"input": 5.00, "cached": 1.25, "output": 20.00}),
    ("gpt-5-codex", {"input": 2.50, "cached": 0.62, "output": 10.00}),
    ("gpt-5", {"input": 2.50, "cached": 0.62, "output": 10.00}),
    ("gpt-4.1", {"input": 2.00, "cached": 0.50, "output": 8.00}),
    ("gpt-4o", {"input": 2.50, "cached": 1.25, "output": 10.00}),
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Token counts for a single model.

    For Claude: cache_write_tokens and cache_read_tokens are separate.
    For Codex: cache_read_tokens holds cached_input_tokens (subtracted
    from input_tokens for cost), cache_write_tokens stays 0.
    For OpenCode: cost is pre-calculated by the tool, stored in
    precalculated_cost. Token counts are informational only.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    precalculated_cost: float = 0.0


@dataclass(frozen=True, slots=True)
class DayEntry:
    """Usage for one calendar day."""

    date: str
    sessions: int = 0
    by_model: tuple[tuple[str, ModelUsage], ...] = ()
    by_session: tuple[tuple[str, tuple[tuple[str, ModelUsage], ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class AiUsageState:
    """Rolling usage history, newest-first."""

    days: tuple[DayEntry, ...] = ()


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------


OPENCODE_PREFIX = "opencode:"


def provider_for_model(model: str) -> Provider:
    """Determine provider from a model name."""
    if model.startswith(OPENCODE_PREFIX):
        return Provider.OPENCODE
    if model.lower().startswith("gpt"):
        return Provider.CODEX
    return Provider.CLAUDE


def dominant_provider(state: AiUsageState) -> Provider | None:
    """Return the provider with higher cost today, or None if no usage."""
    entry = _today_entry(state=state)
    if not entry or not entry.by_model:
        return None
    costs: dict[Provider, float] = {}
    for model, usage in entry.by_model:
        p = provider_for_model(model=model)
        costs[p] = costs.get(p, 0.0) + cost_for_usage(model=model, usage=usage)
    if not costs:
        return None
    return max(costs, key=lambda p: costs[p])


# ---------------------------------------------------------------------------
# Model tier matching
# ---------------------------------------------------------------------------


def match_model_tier(model: str) -> str | None:
    """Match a model string to a pricing tier by substring."""
    lower = model.lower()
    for tier, _p in CLAUDE_PRICING:
        if tier in lower:
            return tier
    for tier, _p in CODEX_PRICING:
        if tier in lower:
            return tier
    return None


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


def cost_for_usage(model: str, usage: ModelUsage) -> float:
    """Compute dollar cost for a single model's token usage."""
    if model.startswith(OPENCODE_PREFIX):
        return usage.precalculated_cost

    lower = model.lower()

    # Claude pricing: separate cache_write and cache_read.
    for tier, p in CLAUDE_PRICING:
        if tier in lower:
            return (
                usage.input_tokens * p["input"]
                + usage.output_tokens * p["output"]
                + usage.cache_write_tokens * p["cache_write"]
                + usage.cache_read_tokens * p["cache_read"]
            ) / 1_000_000

    # Codex pricing: cached tokens subtracted from input.
    for tier, p in CODEX_PRICING:
        if tier in lower:
            non_cached = max(0, usage.input_tokens - usage.cache_read_tokens)
            return (
                non_cached * p["input"]
                + usage.cache_read_tokens * p["cached"]
                + usage.output_tokens * p["output"]
            ) / 1_000_000

    return 0.0


def day_cost(entry: DayEntry) -> float:
    """Total cost for a single day across all models."""
    return sum(cost_for_usage(model=m, usage=u) for m, u in entry.by_model)


def provider_cost(entry: DayEntry, provider: Provider) -> float:
    """Cost for a specific provider in a day entry."""
    return sum(
        cost_for_usage(model=m, usage=u)
        for m, u in entry.by_model
        if provider_for_model(model=m) == provider
    )


def today_cost(state: AiUsageState) -> float:
    entry = _today_entry(state=state)
    return day_cost(entry=entry) if entry else 0.0


def week_cost(state: AiUsageState) -> float:
    return sum(day_cost(entry=d) for d in state.days)


def today_sessions(state: AiUsageState) -> int:
    entry = _today_entry(state=state)
    return entry.sessions if entry else 0


# ---------------------------------------------------------------------------
# State mutations (pure)
# ---------------------------------------------------------------------------


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _today_entry(state: AiUsageState) -> DayEntry | None:
    today = _today_iso()
    for d in state.days:
        if d.date == today:
            return d
    return None


def _merge_usage(a: ModelUsage, b: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_write_tokens=a.cache_write_tokens + b.cache_write_tokens,
        cache_read_tokens=a.cache_read_tokens + b.cache_read_tokens,
        precalculated_cost=a.precalculated_cost + b.precalculated_cost,
    )


def set_session(
    state: AiUsageState,
    session_id: str,
    model_usage: dict[str, ModelUsage],
) -> AiUsageState:
    """Replace a session's usage in today's entry (idempotent)."""
    today = _today_iso()

    existing: DayEntry | None = None
    others: list[DayEntry] = []
    for d in state.days:
        if d.date == today:
            existing = d
        else:
            others.append(d)

    if existing is None:
        existing = DayEntry(date=today)

    # Replace this session's data in by_session, then rebuild by_model.
    by_session = dict(existing.by_session)
    by_session[session_id] = tuple(model_usage.items())

    # Aggregate across all sessions.
    merged: dict[str, ModelUsage] = {}
    for session_models in by_session.values():
        for model, usage in session_models:
            prev = merged.get(model, ModelUsage())
            merged[model] = _merge_usage(prev, usage)

    updated = replace(
        existing,
        sessions=len(by_session),
        by_model=tuple(merged.items()),
        by_session=tuple(by_session.items()),
    )

    days = (updated, *others[: MAX_DAYS - 1])
    return AiUsageState(days=days)


def reset_today(state: AiUsageState) -> AiUsageState:
    today = _today_iso()
    return AiUsageState(days=tuple(d for d in state.days if d.date != today))


# ---------------------------------------------------------------------------
# Prefs serialization
# ---------------------------------------------------------------------------


def _parse_model_counts(raw: dict[str, Any]) -> list[tuple[str, ModelUsage]]:
    result: list[tuple[str, ModelUsage]] = []
    for model, counts in raw.items():
        if not isinstance(counts, dict):
            continue
        result.append(
            (
                model,
                ModelUsage(
                    input_tokens=int(counts.get("in", 0)),
                    output_tokens=int(counts.get("out", 0)),
                    cache_write_tokens=int(counts.get("cw", 0)),
                    cache_read_tokens=int(counts.get("cr", 0)),
                    precalculated_cost=float(counts.get("pc", 0)),
                ),
            )
        )
    return result


def state_from_prefs(prefs: Mapping[str, Any] | None) -> AiUsageState:
    """Deserialize from applet_prefs dict."""
    if not prefs:
        return AiUsageState()
    raw_days = prefs.get("days")
    if not isinstance(raw_days, list):
        return AiUsageState()

    days: list[DayEntry] = []
    for rd in raw_days:
        if not isinstance(rd, dict) or "date" not in rd:
            continue

        # Parse per-session data if available.
        by_session: list[tuple[str, tuple[tuple[str, ModelUsage], ...]]] = []
        raw_bs = rd.get("by_session", {})
        if isinstance(raw_bs, dict):
            for sid, raw_models in raw_bs.items():
                if isinstance(raw_models, dict):
                    by_session.append((sid, tuple(_parse_model_counts(raw_models))))

        # If we have by_session, derive by_model from it.
        if by_session:
            merged: dict[str, ModelUsage] = {}
            for _sid, session_models in by_session:
                for model, usage in session_models:
                    prev = merged.get(model, ModelUsage())
                    merged[model] = _merge_usage(prev, usage)
            by_model = list(merged.items())
            session_count = len(by_session)
        else:
            # Legacy format: only by_model, no by_session.
            by_model = _parse_model_counts(rd.get("by_model", {}))
            session_count = int(rd.get("sessions", 0))

        days.append(
            DayEntry(
                date=str(rd["date"]),
                sessions=session_count,
                by_model=tuple(by_model),
                by_session=tuple(by_session),
            )
        )
    return AiUsageState(days=tuple(days[:MAX_DAYS]))


def _serialize_model_usage(
    models: tuple[tuple[str, ModelUsage], ...],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for model, u in models:
        d: dict[str, Any] = {
            "in": u.input_tokens,
            "out": u.output_tokens,
            "cw": u.cache_write_tokens,
            "cr": u.cache_read_tokens,
        }
        if u.precalculated_cost:
            d["pc"] = u.precalculated_cost
        result[model] = d
    return result


def prefs_from_state(state: AiUsageState) -> dict[str, Any]:
    """Serialize to applet_prefs dict."""
    days: list[dict[str, Any]] = []
    for d in state.days:
        entry: dict[str, Any] = {
            "date": d.date,
            "sessions": d.sessions,
            "by_model": _serialize_model_usage(d.by_model),
        }
        if d.by_session:
            entry["by_session"] = {
                sid: _serialize_model_usage(models) for sid, models in d.by_session
            }
        days.append(entry)
    return {"days": days}


# ---------------------------------------------------------------------------
# Tooltip
# ---------------------------------------------------------------------------


def _format_cost(cost: float) -> str:
    if cost >= 1.0:
        return f"${cost:.2f}"
    if cost > 0:
        return f"${cost:.3f}"
    return "$0"


def _short_model(model: str) -> str:
    """Extract a short display name from a full model ID."""
    raw = model.removeprefix(OPENCODE_PREFIX)
    tier = match_model_tier(model=raw)
    return tier.capitalize() if tier else raw


def tooltip_text(state: AiUsageState, provider: Provider | None = None) -> str:
    """Simple single-line fallback tooltip."""
    entry = _today_entry(state=state)
    if provider and entry:
        cost = provider_cost(entry=entry, provider=provider)
        name = provider.value.capitalize()
    else:
        cost = today_cost(state=state)
        name = "AI Usage"
    sessions = today_sessions(state=state)
    if sessions == 0 and cost <= 0:
        return _("{name}: no usage today").format(name=name)
    return _("{name} today: {cost}").format(name=name, cost=_format_cost(cost=cost))


# ---------------------------------------------------------------------------
# Claude transcript parsing
# ---------------------------------------------------------------------------


def _has_usage(u: ModelUsage) -> bool:
    return bool(
        u.input_tokens
        or u.output_tokens
        or u.cache_write_tokens
        or u.cache_read_tokens
        or u.precalculated_cost
    )


def parse_claude_transcript(path: Path) -> dict[str, ModelUsage]:
    """Read a Claude Code JSONL transcript and accumulate per-model usage."""
    result: dict[str, ModelUsage] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return result

    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
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


# ---------------------------------------------------------------------------
# Codex transcript parsing
# ---------------------------------------------------------------------------


def find_codex_session(thread_id: str | None = None) -> Path | None:
    """Find a Codex session JSONL file by thread-id or most recent."""
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.is_dir():
        return None

    if thread_id:
        for jsonl in sorted(sessions_dir.rglob("*.jsonl"), reverse=True):
            if thread_id in jsonl.name:
                return jsonl

    # Fallback: most recent by mtime.
    candidates = sorted(
        sessions_dir.rglob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def parse_codex_transcript(path: Path) -> dict[str, ModelUsage]:
    """Read a Codex CLI JSONL session and extract cumulative usage."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    model: str = ""
    best_total: int = 0
    best_usage: dict[str, int] = {}

    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
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


# ---------------------------------------------------------------------------
# OpenCode usage (SQLite database)
# ---------------------------------------------------------------------------

_OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def query_opencode_today() -> dict[str, dict[str, ModelUsage]]:
    """Query OpenCode SQLite for today's sessions.

    Returns {session_id: {prefixed_model: ModelUsage}}.
    """
    import sqlite3

    if not _OPENCODE_DB.exists():
        return {}

    today = _today_iso()
    # time_created is Unix ms; compute start-of-day in ms.
    today_start_ms = int(datetime.datetime.fromisoformat(today).timestamp() * 1000)

    try:
        conn = sqlite3.connect(f"file:{_OPENCODE_DB}?mode=ro", uri=True)
    except sqlite3.OperationalError:
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

        result: dict[str, dict[str, ModelUsage]] = {}
        for sid in session_ids:
            cur.execute("SELECT data FROM message WHERE session_id = ?", (sid,))
            by_model: dict[str, ModelUsage] = {}
            for (data_str,) in cur.fetchall():
                try:
                    data = json.loads(data_str)
                except (json.JSONDecodeError, ValueError):
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
                result[sid] = by_model
        return result
    finally:
        conn.close()
