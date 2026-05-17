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
