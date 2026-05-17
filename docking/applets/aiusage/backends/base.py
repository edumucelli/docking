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

from typing import Protocol

from docking.applets.aiusage.state import ModelUsage, Provider

ProviderSessions = dict[str, dict[str, ModelUsage]]


class UsageBackend(Protocol):
    provider: Provider

    def register_hooks(self) -> None:
        """Install provider hooks when supported."""

    def poll_today(self) -> ProviderSessions:
        """Return today's provider sessions discovered from local storage."""

    def handle_hook(self, *, event: str, payload: object) -> None:
        """Handle one provider hook invocation."""
