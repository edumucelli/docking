"""Process lifetime retained by application launchers on every platform."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class LaunchedProcess(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...


@dataclass(frozen=True, slots=True)
class LaunchOutcome:
    started: bool
    process: LaunchedProcess | None = None
    executable_path: Path | None = None
