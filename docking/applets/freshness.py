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


def updated_label(
    timestamp: dt.datetime | str | None,
    *,
    now: dt.datetime | None = None,
) -> str:
    """Return a relative updated label, or an empty string when unknown."""
    age = relative_time_label(timestamp=timestamp, now=now)
    if not age:
        return ""
    return _("Updated: {age}").format(age=age)


def relative_time_label(
    timestamp: dt.datetime | str | None,
    *,
    now: dt.datetime | None = None,
) -> str:
    """Return a human relative age such as "5 minutes ago"."""
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return ""
    reference = now or dt.datetime.now(dt.timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=dt.timezone.utc)
    elapsed = reference.astimezone(dt.timezone.utc) - parsed.astimezone(dt.timezone.utc)
    elapsed_seconds = max(0, int(elapsed.total_seconds()))
    if elapsed_seconds == 0:
        return _("just now")
    return _("{age} ago").format(age=_format_relative_interval(elapsed_seconds))


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


def _format_relative_interval(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return (
            _("1 second")
            if seconds == 1
            else _("{count} seconds").format(count=seconds)
        )
    minutes = seconds // 60
    if minutes < 60:
        return (
            _("1 minute")
            if minutes == 1
            else _("{count} minutes").format(count=minutes)
        )
    hours = minutes // 60
    if hours < 24:
        return _("1 hour") if hours == 1 else _("{count} hours").format(count=hours)
    days = hours // 24
    return _("1 day") if days == 1 else _("{count} days").format(count=days)
