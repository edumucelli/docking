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
from docking.ui.tooltip import relative_time_label


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


def format_local_time(timestamp: dt.datetime) -> str:
    """Format a timestamp in the user's local timezone."""
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M")
