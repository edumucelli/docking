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

"""Pure state and cost logic for AI usage tracker applet."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from docking.applets.tooltip import structured_tooltip
from docking.i18n import _

MAX_DAYS = 7


class Provider(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"


class DisplayMode(str, Enum):
    COST = "cost"
    TOKENS = "tokens"


# ---------------------------------------------------------------------------
# Pricing tables
# ---------------------------------------------------------------------------

# Claude: input, output, cache_write (1.25x), cache_read (0.1x).
# Ordered most-specific first so versioned matches win.
_OPUS46 = {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50}
_OPUS4 = {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50}

CLAUDE_PRICING: tuple[tuple[str, dict[str, float]], ...] = (
    ("opus-4-8", _OPUS46),
    ("opus-4-7", _OPUS46),
    ("opus-4-6", _OPUS46),
    ("opus-4-5", _OPUS46),
    ("opus-4-1", _OPUS4),
    ("opus-4", _OPUS4),
    ("sonnet-4-6", {"input": 3, "output": 15, "cache_write": 3.75, "cache_read": 0.30}),
    ("sonnet-4-5", {"input": 3, "output": 15, "cache_write": 3.75, "cache_read": 0.30}),
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
# Token aggregation
# ---------------------------------------------------------------------------


def total_tokens(usage: ModelUsage) -> int:
    """Total fresh tokens (non-cached input + output)."""
    fresh_input = max(0, usage.input_tokens - usage.cache_read_tokens)
    return fresh_input + usage.output_tokens


def day_tokens(entry: DayEntry) -> int:
    return sum(total_tokens(u) for _, u in entry.by_model)


def provider_tokens(entry: DayEntry, provider: Provider) -> int:
    return sum(
        total_tokens(u)
        for m, u in entry.by_model
        if provider_for_model(model=m) == provider
    )


def today_tokens(state: AiUsageState) -> int:
    entry = _today_entry(state=state)
    return day_tokens(entry=entry) if entry else 0


def week_tokens(state: AiUsageState) -> int:
    return sum(day_tokens(entry=d) for d in state.days)


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


def _format_tokens(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}K"
    return str(tokens)


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
        return structured_tooltip(
            title=name,
            primary=_("no usage today"),
        )
    return structured_tooltip(
        title=name,
        primary=_("Today: {cost}").format(cost=_format_cost(cost=cost)),
    )


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
