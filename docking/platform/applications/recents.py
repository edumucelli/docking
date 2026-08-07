"""Recent-application records, policy, and config persistence."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from docking.log import get_logger, with_context

from .registry import ApplicationRegistry
from .types import ApplicationOrigin

RecentApplicationsListener = Callable[[], None]
Clock = Callable[[], float]

log = with_context(get_logger(name="recent_applications"))


@dataclass(frozen=True, slots=True, kw_only=True)
class RecentAppRecord:
    """One ordered recent-application entry."""

    desktop_id: str
    last_closed: int


class RecentApplicationsConfig(Protocol):
    """Config surface required by the recent-applications adapter."""

    show_recent_apps: bool
    recent_apps_max: int
    recent_apps_retention_days: int
    recent_apps: Any

    def save(self) -> None: ...


class ApplicationResolver(Protocol):
    """Read-only registry surface used by recent-application policy."""

    def resolve(
        self,
        desktop_id: str,
        *,
        log_failures: bool = True,
    ) -> Any: ...


class RecentApplicationsPersistence:
    """Own the config wire format and its explicit save operation."""

    def __init__(self, config: RecentApplicationsConfig) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return bool(self._config.show_recent_apps)

    @property
    def max_count(self) -> int:
        return int(self._config.recent_apps_max)

    @property
    def retention_days(self) -> int:
        return int(self._config.recent_apps_retention_days)

    def read(self) -> tuple[RecentAppRecord, ...]:
        """Decode valid records from the config-owned wire value."""
        raw_records = self._config.recent_apps
        if not isinstance(raw_records, list):
            return ()

        records: list[RecentAppRecord] = []
        for raw in raw_records:
            if not isinstance(raw, dict):
                continue
            desktop_id = raw.get("desktop_id")
            last_closed = raw.get("last_closed")
            if (
                not isinstance(desktop_id, str)
                or not desktop_id
                or not isinstance(last_closed, int | float)
            ):
                continue
            try:
                if not math.isfinite(last_closed):
                    continue
                normalized_last_closed = int(last_closed)
            except (OverflowError, ValueError):
                continue
            records.append(
                RecentAppRecord(
                    desktop_id=desktop_id,
                    last_closed=normalized_last_closed,
                )
            )
        return tuple(records)

    def replace(self, records: Iterable[RecentAppRecord]) -> None:
        """Encode records into the exact config wire shape without saving."""
        self._config.recent_apps = [
            {
                "desktop_id": record.desktop_id,
                "last_closed": record.last_closed,
            }
            for record in records
        ]

    def save(self, records: Iterable[RecentAppRecord]) -> None:
        """Replace the config value and flush the whole config."""
        self.replace(records)
        self._config.save()


class RecentApplications:
    """Own ordered recent-application records and their mutation policy."""

    def __init__(
        self,
        registry: ApplicationRegistry | ApplicationResolver,
        persistence: RecentApplicationsPersistence | RecentApplicationsConfig,
        *,
        clock: Clock = time.time,
    ) -> None:
        self._registry = registry
        self._persistence = (
            persistence
            if isinstance(persistence, RecentApplicationsPersistence)
            else RecentApplicationsPersistence(persistence)
        )
        self._clock = clock
        self._records: list[RecentAppRecord] = []
        self._previous_running_ids: set[str] = set()
        self._listeners: list[RecentApplicationsListener] = []
        self._loaded = False
        self._enabled = self._persistence.enabled

    def load(self, *, pinned_ids: Iterable[str] = ()) -> None:
        """Load, filter, and memory-sync records without flushing config."""
        self._loaded = True
        self._enabled = self._persistence.enabled
        if not self._enabled:
            self._records = []
            self._notify_listeners()
            return

        pinned = set(pinned_ids)
        cutoff = self._retention_cutoff()
        seen: set[str] = set()
        records: list[RecentAppRecord] = []
        for record in self._persistence.read():
            if (
                record.desktop_id in pinned
                or record.desktop_id in seen
                or record.last_closed < cutoff
                or not self._is_resolvable(record.desktop_id)
            ):
                continue
            seen.add(record.desktop_id)
            records.append(record)

        self._records = records[: self._persistence.max_count]
        self._persistence.replace(self._records)
        self._notify_listeners()

    def reload_policy(self, *, pinned_ids: Iterable[str] = ()) -> None:
        """Apply an enabled-state transition or announce a policy change.

        Max-count and retention changes intentionally do not prune or save here.
        They take effect on the next normal pruning operation, matching the
        pre-service settings behavior.
        """
        enabled = self._persistence.enabled
        if not self._loaded:
            self.load(pinned_ids=pinned_ids)
            return
        if not enabled:
            self._enabled = False
            self.clear()
            return
        if not self._enabled:
            self.load(pinned_ids=pinned_ids)
            return

        self._enabled = True
        self._notify_listeners()

    def reconcile_running(
        self,
        running_ids: Iterable[str],
        *,
        pinned_ids: Iterable[str] = (),
    ) -> None:
        """Record closures and suppress records for applications now running."""
        current_ids = set(running_ids)
        previous_ids = self._previous_running_ids
        self._previous_running_ids = current_ids
        if not self._persistence.enabled:
            self._enabled = False
            return

        self._enabled = True
        pinned = set(pinned_ids)
        for desktop_id in previous_ids - current_ids:
            self.record_closed(desktop_id, pinned_ids=pinned)

        if not current_ids:
            return
        records = [
            record for record in self._records if record.desktop_id not in current_ids
        ]
        if records == self._records:
            return
        self._records = records
        self._persistence.replace(self._records)
        self._notify_listeners()

    def record_closed(
        self,
        desktop_id: str,
        *,
        pinned_ids: Iterable[str] = (),
    ) -> bool:
        """Record one newly closed application, saving before policy pruning."""
        if desktop_id in set(pinned_ids):
            return False
        return self._record(desktop_id)

    def record_unpinned(self, desktop_id: str) -> bool:
        """Record one non-running application removed from the pinned section."""
        return self._record(desktop_id)

    def discard(self, desktop_id: str) -> bool:
        """Remove one record and memory-sync config without saving."""
        records = [
            record for record in self._records if record.desktop_id != desktop_id
        ]
        if records == self._records:
            return False
        self._records = records
        self._persistence.replace(self._records)
        self._notify_listeners()
        return True

    def clear(self) -> None:
        """Clear all records and save immediately."""
        self._records = []
        self._persistence.save(self._records)
        self._notify_listeners()

    def snapshot(self) -> tuple[RecentAppRecord, ...]:
        """Return the current immutable ordered record snapshot."""
        return tuple(self._records)

    def subscribe(
        self,
        callback: RecentApplicationsListener,
    ) -> Callable[[], None]:
        """Register a listener once and return an idempotent unsubscribe."""
        if callback not in self._listeners:
            self._listeners.append(callback)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if not subscribed:
                return
            subscribed = False
            with suppress(ValueError):
                self._listeners.remove(callback)

        return unsubscribe

    def _record(self, desktop_id: str) -> bool:
        if (
            not self._persistence.enabled
            or not isinstance(desktop_id, str)
            or not desktop_id
            or not self._is_resolvable(desktop_id)
        ):
            return False

        self._enabled = True
        record = RecentAppRecord(
            desktop_id=desktop_id,
            last_closed=int(self._clock()),
        )
        records = [
            existing for existing in self._records if existing.desktop_id != desktop_id
        ]
        records.insert(0, record)
        self._records = records

        # Persist before pruning to preserve the existing saved-over-cap quirk.
        self._persistence.save(self._records)
        self._prune()
        self._notify_listeners()
        return True

    def _prune(self) -> None:
        cutoff = self._retention_cutoff()
        retained = [record for record in self._records if record.last_closed >= cutoff]
        self._records = retained[: self._persistence.max_count]

    def _retention_cutoff(self) -> float:
        return self._clock() - (self._persistence.retention_days * 86400)

    def _is_resolvable(self, desktop_id: str) -> bool:
        application = self._registry.resolve(desktop_id, log_failures=False)
        return application is not None and (
            getattr(application, "origin", None) is not ApplicationOrigin.RUNTIME
        )

    def _notify_listeners(self) -> None:
        for callback in tuple(self._listeners):
            try:
                callback()
            except Exception as exc:
                log.bind(action="notify_recent_applications_listener").warning(
                    "Recent-applications listener failed: %s",
                    exc,
                )


__all__ = [
    "ApplicationResolver",
    "RecentAppRecord",
    "RecentApplications",
    "RecentApplicationsConfig",
    "RecentApplicationsListener",
    "RecentApplicationsPersistence",
]
