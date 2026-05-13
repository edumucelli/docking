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
