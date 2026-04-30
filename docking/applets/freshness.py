"""Shared freshness and update-cadence text for applets."""

from __future__ import annotations

import datetime as dt

from docking.i18n import _


def format_interval(seconds: int) -> str:
    """Return a compact human interval for menu and tooltip text."""
    seconds = max(1, int(seconds))
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return _("1 hour") if hours == 1 else _("{count} hours").format(count=hours)
    if seconds % 60 == 0:
        minutes = seconds // 60
        return (
            _("1 minute")
            if minutes == 1
            else _("{count} minutes").format(count=minutes)
        )
    return _("1 second") if seconds == 1 else _("{count} seconds").format(count=seconds)


def cadence_label(*, seconds: int, verb: str | None = None) -> str:
    """Return "<Verb> every <interval>"."""
    return _("{verb} every {interval}").format(
        verb=verb or _("Updates"),
        interval=format_interval(seconds),
    )


def on_demand_label(*, verb: str | None = None) -> str:
    """Return "<Verb> on demand" for manual applets."""
    return _("{verb} on demand").format(verb=verb or _("Updates"))


def updated_label(timestamp: dt.datetime | str | None) -> str:
    """Return a local-time updated label, or an empty string when unknown."""
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return ""
    return _("Updated: {time}").format(time=format_local_time(parsed))


def parse_timestamp(timestamp: dt.datetime | str | None) -> dt.datetime | None:
    """Parse an aware UTC/local timestamp into a timezone-aware datetime."""
    if timestamp is None:
        return None
    if isinstance(timestamp, dt.datetime):
        parsed = timestamp
    else:
        text = str(timestamp).strip()
        if not text:
            return None
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def format_local_time(timestamp: dt.datetime) -> str:
    """Format a timestamp in the user's local timezone."""
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M")
