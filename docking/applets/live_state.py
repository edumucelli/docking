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

"""Shared loading, stale, and error state helpers for live applets."""

from __future__ import annotations

import datetime as dt
from enum import Enum

from docking.applets.freshness import (
    cadence_label,
    format_local_time,
    parse_timestamp,
    updated_label,
)
from docking.i18n import _


class LiveDataStatus(str, Enum):
    """Common status vocabulary for applets backed by changing data."""

    EMPTY = "empty"
    LOADING = "loading"
    READY = "ready"
    STALE = "stale"
    ERROR = "error"


def resolve_live_status(
    *,
    has_data: bool,
    loading: bool = False,
    error: str | None = None,
    updated_at: dt.datetime | str | None = None,
    stale_after_seconds: int | None = None,
    now: dt.datetime | None = None,
) -> LiveDataStatus:
    """Classify a live applet without losing old usable data."""
    if loading and not has_data:
        return LiveDataStatus.LOADING
    if _clean_error(error):
        return LiveDataStatus.STALE if has_data else LiveDataStatus.ERROR
    if has_data:
        if is_stale(
            updated_at,
            max_age_seconds=stale_after_seconds,
            now=now,
        ):
            return LiveDataStatus.STALE
        return LiveDataStatus.READY
    return LiveDataStatus.EMPTY


def is_stale(
    updated_at: dt.datetime | str | None,
    *,
    max_age_seconds: int | None,
    now: dt.datetime | None = None,
) -> bool:
    """Return true when ``updated_at`` is older than the allowed age."""
    if max_age_seconds is None or max_age_seconds <= 0:
        return False
    parsed = parse_timestamp(updated_at)
    if parsed is None:
        return False
    reference = now or dt.datetime.now(dt.timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=dt.timezone.utc)
    age = reference.astimezone(dt.timezone.utc) - parsed.astimezone(dt.timezone.utc)
    return age.total_seconds() > max_age_seconds


def live_state_label(status: LiveDataStatus) -> str:
    """Return the compact user-facing label for a live-data state."""
    return {
        LiveDataStatus.EMPTY: _("No data yet"),
        LiveDataStatus.LOADING: _("Loading..."),
        LiveDataStatus.READY: "",
        LiveDataStatus.STALE: _("Stale data"),
        LiveDataStatus.ERROR: _("Unavailable"),
    }[status]


def live_state_error(
    *,
    status: LiveDataStatus,
    error: str | None,
) -> str | None:
    """Expose error details only for states where they matter."""
    if status not in (LiveDataStatus.ERROR, LiveDataStatus.STALE):
        return None
    return _clean_error(error) or None


def live_freshness_lines(
    *,
    status: LiveDataStatus,
    updated_at: dt.datetime | str | None = None,
    cadence_seconds: int | None = None,
    cadence_verb: str | None = None,
) -> tuple[str, ...]:
    """Build standard freshness/cadence lines for structured tooltips."""
    lines: list[str] = []
    if status is LiveDataStatus.STALE:
        lines.append(stale_label(updated_at))
    else:
        updated = updated_label(updated_at)
        if updated:
            lines.append(updated)
    if cadence_seconds:
        lines.append(cadence_label(seconds=cadence_seconds, verb=cadence_verb))
    return tuple(line for line in lines if line)


def stale_label(updated_at: dt.datetime | str | None = None) -> str:
    """Return a standard stale-data line, with last update when known."""
    parsed = parse_timestamp(updated_at)
    if parsed is None:
        return _("Stale data")
    return _("Stale: last updated {time}").format(time=format_local_time(parsed))


def refresh_recovery_label(status: LiveDataStatus) -> str | None:
    """Return a standard recovery hint for non-ready live-data states."""
    if status in (
        LiveDataStatus.EMPTY,
        LiveDataStatus.STALE,
        LiveDataStatus.ERROR,
    ):
        return _("Use Refresh Now from the menu.")
    return None


def _clean_error(error: str | None) -> str:
    return str(error or "").strip()
