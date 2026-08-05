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

"""Typed running-window state shared by window tracking and the dock model."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from docking.platform.backends.base import WindowId


@dataclass(frozen=True)
class RuntimeAppIdentity:
    """Metadata for a running executable without a matching desktop entry."""

    desktop_id: str
    executable_path: str
    name: str
    icon_name: str
    wm_class: str


@dataclass(frozen=True)
class RunningWindowInfo:
    """One matched live window from the latest tracker scan."""

    # desktop_id is the model identity after WM_CLASS matching. It is not
    # necessarily derived directly from the window; WindowMatcher may resolve it
    # through desktop-file candidates or the launcher WM_CLASS index.
    desktop_id: str
    # Keep the raw XID alongside the Wnck object because hover previews and
    # focus actions use XIDs as a stable handoff between scans. Wnck objects
    # themselves can become stale at any time.
    xid: int
    # Backend-neutral ID for future non-X11 window services. X11 callers should
    # keep using xid until the UI is migrated to WindowService methods.
    window_id: WindowId
    # These are per-window booleans. RunningAppInfo folds them with any(), so one
    # active or urgent window makes the app active or urgent.
    active: bool
    urgent: bool
    # The live Wnck window is intentionally carried through for callers that
    # need titles, focus, or close/minimize operations. Do not serialize it.
    window: Any
    # Present only when strong executable evidence deliberately separated this
    # window from a family-level desktop entry.
    runtime_app: RuntimeAppIdentity | None = None


@dataclass(frozen=True)
class RunningAppInfo:
    """Aggregated running state for one desktop ID."""

    # count mirrors how many valid window snapshots survived the scan. It can be
    # zero when a window matched a desktop ID but vanished before its XID could
    # be read; that preserves the old WindowTracker aggregate behavior.
    count: int = 0
    active: bool = False
    urgent: bool = False
    # Tuples make the aggregate immutable once published to DockModel. This
    # avoids accidental mutation between tracker scan, model reconciliation, and
    # UI consumers.
    windows: tuple[Any, ...] = ()
    xids: tuple[int, ...] = ()
    window_ids: tuple[WindowId, ...] = ()
    runtime_app: RuntimeAppIdentity | None = None

    @classmethod
    def from_windows(cls, windows: Iterable[RunningWindowInfo]) -> RunningAppInfo:
        """Build one app aggregate from matched window snapshots."""
        # Materialize once because the iterable feeds count, folded booleans,
        # windows, and XIDs. Keeping all fields derived from the same snapshot
        # tuple prevents subtle mismatches if a generator were consumed twice.
        snapshots = tuple(windows)
        runtime_app = next(
            (
                snapshot.runtime_app
                for snapshot in snapshots
                if snapshot.runtime_app is not None
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
