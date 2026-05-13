"""AI usage provider backends."""

from __future__ import annotations

from docking.applets.aiusage.backends.base import UsageBackend
from docking.applets.aiusage.backends.claude import ClaudeBackend
from docking.applets.aiusage.backends.codex import CodexBackend
from docking.applets.aiusage.backends.opencode import OpenCodeBackend
from docking.applets.aiusage.state import Provider

BACKENDS: tuple[UsageBackend, ...] = (
    ClaudeBackend(),
    CodexBackend(),
    OpenCodeBackend(),
)

BACKENDS_BY_PROVIDER = {backend.provider: backend for backend in BACKENDS}


def backend_for_name(name: str) -> UsageBackend | None:
    try:
        provider = Provider(name)
    except ValueError:
        return None
    return BACKENDS_BY_PROVIDER.get(provider)
