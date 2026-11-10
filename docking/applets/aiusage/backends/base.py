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

"""Backend contract for AI usage providers."""

from __future__ import annotations

import datetime
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

from docking.applets.aiusage.state import ModelUsage, Provider

ProviderSessions = dict[str, dict[str, ModelUsage]]


def transcript_entries(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


def event_date(value: object, *, fallback: str) -> str | None:
    # shortcut: Undated events use the observed day, until legacy logs are retired.
    if value is None:
        return fallback
    if not isinstance(value, str):
        return None
    try:
        return (
            datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            .astimezone()
            .date()
            .isoformat()
        )
    except (ValueError, OverflowError, OSError):
        return None


class UsageBackend(Protocol):
    provider: Provider

    def register_hooks(self) -> None:
        """Install provider hooks when supported."""

    def poll_today(self) -> ProviderSessions:
        """Return today's provider sessions discovered from local storage."""

    def handle_hook(self, *, event: str, payload: object) -> None:
        """Handle one provider hook invocation."""
