"""Recent-applications tracking service.

The ``RecentApplications`` class is the single owner of the recent-apps
list.  ``DockModel`` and settings talk to this service instead of
mutating ``Config.recent_apps`` directly.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RecentAppRecord:
    desktop_id: str
    last_closed: int


@dataclass(frozen=True, slots=True)
class RecentApplicationsPolicy:
    enabled: bool
    maximum: int
    retention_days: int


class RecentApplicationsPersistence(Protocol):
    def decode(self, raw: list[dict]) -> list[RecentAppRecord]: ...
    def encode(self, records: Sequence[RecentAppRecord]) -> list[dict]: ...
    def save(self, records: Sequence[RecentAppRecord]) -> None: ...


class RecentApplications:
    """Single owner of the recent-apps list."""

    def __init__(
        self,
        *,
        persistence: RecentApplicationsPersistence | None = None,
        policy: RecentApplicationsPolicy | None = None,
    ) -> None:
        self._records: list[RecentAppRecord] = []
        self._listeners: list[Callable[[], None]] = []
        self._policy = policy or RecentApplicationsPolicy(
            enabled=True,
            maximum=5,
            retention_days=14,
        )
        self._persistence = persistence
        self._prev_running_ids: set[str] = set()

    def load(self, raw_records: list[dict], *, pinned_ids: set[str]) -> None:
        if not self._policy.enabled:
            self._records.clear()
            return
        cutoff = int(time.time()) - (self._policy.retention_days * 86400)
        seen: set[str] = set()
        records: list[RecentAppRecord] = []
        if self._persistence is not None:
            decoded = self._persistence.decode(raw_records)
        else:
            decoded = [
                RecentAppRecord(
                    desktop_id=str(r.get("desktop_id", "")),
                    last_closed=int(r.get("last_closed", 0)),
                )
                for r in raw_records
            ]
        for record in decoded:
            if not record.desktop_id:
                continue
            if record.desktop_id in pinned_ids or record.desktop_id in seen:
                continue
            if record.last_closed < cutoff:
                continue
            seen.add(record.desktop_id)
            records.append(record)
        changed = records != self._records
        self._records = records
        if changed:
            self._notify()

    def reconcile_running(self, current_ids: set[str], *, pinned_ids: set[str]) -> bool:
        if not self._policy.enabled:
            return False
        closed_ids = self._prev_running_ids - current_ids
        self._prev_running_ids = set(current_ids)
        changed = False
        for desktop_id in closed_ids:
            if desktop_id in pinned_ids:
                continue
            changed = True
            self.record_closed(desktop_id)
        return changed

    def record_closed(self, desktop_id: str) -> None:
        for i, record in enumerate(self._records):
            if record.desktop_id == desktop_id:
                self._records.pop(i)
                self._records.insert(0, RecentAppRecord(desktop_id, int(time.time())))
                self._persist()
                self._prune()
                self._notify()
                return
        self._records.insert(0, RecentAppRecord(desktop_id, int(time.time())))
        self._persist()
        self._prune()
        self._notify()

    def clear(self) -> None:
        if not self._records:
            return
        self._records.clear()
        self._persist()
        self._notify()

    def update_policy(
        self, policy: RecentApplicationsPolicy, *, pinned_ids: set[str]
    ) -> None:
        self._policy = policy
        if not policy.enabled:
            self.clear()
            return
        self._prune()
        self._persist()

    def snapshot(self) -> tuple[RecentAppRecord, ...]:
        return tuple(self._records)

    def add_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        with suppress(ValueError):
            self._listeners.remove(callback)

    def _prune(self) -> None:
        cutoff = int(time.time()) - (self._policy.retention_days * 86400)
        kept: list[RecentAppRecord] = []
        for record in self._records:
            if record.last_closed < cutoff:
                continue
            kept.append(record)
        self._records = kept[: self._policy.maximum]

    def _persist(self) -> None:
        if self._persistence is not None:
            self._persistence.save(tuple(self._records))

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            with suppress(Exception):
                listener()


__all__ = [
    "RecentAppRecord",
    "RecentApplications",
    "RecentApplicationsPersistence",
    "RecentApplicationsPolicy",
]
