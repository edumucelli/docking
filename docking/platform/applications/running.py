"""Binding-free running-window values shared by backends and the dock model."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from docking.platform.backends.base import WindowId

from .types import ApplicationInfo, ApplicationOrigin


@dataclass(frozen=True)
class RunningWindowInfo:
    """One matched live window from the latest backend scan."""

    desktop_id: str
    xid: int
    window_id: WindowId
    active: bool
    urgent: bool
    window: Any
    runtime_app: ApplicationInfo | None = None


@dataclass(frozen=True)
class RunningAppInfo:
    """Aggregated running state for one desktop ID."""

    count: int = 0
    active: bool = False
    urgent: bool = False
    windows: tuple[Any, ...] = ()
    xids: tuple[int, ...] = ()
    window_ids: tuple[WindowId, ...] = ()
    runtime_app: ApplicationInfo | None = None

    @classmethod
    def from_windows(cls, windows: Iterable[RunningWindowInfo]) -> RunningAppInfo:
        """Build one immutable aggregate from a single window snapshot."""
        snapshots = tuple(windows)
        runtime_app = next(
            (
                snapshot.runtime_app
                for snapshot in snapshots
                if snapshot.runtime_app is not None
                and snapshot.runtime_app.origin is ApplicationOrigin.RUNTIME
            ),
            None,
        )
        return cls(
            count=len(snapshots),
            active=any(snapshot.active for snapshot in snapshots),
            urgent=any(snapshot.urgent for snapshot in snapshots),
            windows=tuple(snapshot.window for snapshot in snapshots),
            xids=tuple(snapshot.xid for snapshot in snapshots),
            window_ids=tuple(snapshot.window_id for snapshot in snapshots),
            runtime_app=runtime_app,
        )


__all__ = [
    "RunningAppInfo",
    "RunningWindowInfo",
]
