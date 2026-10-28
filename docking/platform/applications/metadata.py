"""Optional platform catalog and visibility facts, resolved outside GTK.

Gio supplies desktop-file facts on Unix. Other platforms can supplement the
registry with immutable native listings and explicit hidden-ID declarations.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from threading import Event, RLock, Thread

from docking.log import get_logger
from docking.platform.environment import is_windows

from .registration import ExecutableRegistrationBackend
from .types import ApplicationInfo

log = get_logger("application_metadata")


@dataclass(frozen=True, slots=True)
class ApplicationMetadata:
    hidden_ids: frozenset[str] = frozenset()
    # None means the native catalog was unavailable, rather than empty.
    applications: tuple[ApplicationInfo, ...] | None = None


class ApplicationMetadataLookup:
    """Coalesce metadata reads and reject callbacks from obsolete requests."""

    def __init__(
        self,
        *,
        resolve: Callable[[tuple[str, ...]], ApplicationMetadata],
        on_changed: Callable[[], None],
        schedule_idle: Callable[..., int],
        remove_source: Callable[[int], bool],
        refresh_interval: float = 30.0,
    ) -> None:
        self._resolve = resolve
        self._on_changed = on_changed
        self._schedule_idle = schedule_idle
        self._remove_source = remove_source
        self._refresh_interval = refresh_interval
        self._lock = RLock()
        self._started = False
        self._epoch = 0
        self._generation = 0
        self._requested: tuple[str, ...] | None = None
        self._pending: tuple[int, tuple[str, ...]] | None = None
        self._closed = Event()
        self._wake = Event()
        self._sources: set[int] = set()
        self._snapshot = ApplicationMetadata()

    @property
    def hidden_ids(self) -> frozenset[str]:
        return self._snapshot.hidden_ids

    @property
    def applications(self) -> tuple[ApplicationInfo, ...] | None:
        return self._snapshot.applications

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._epoch += 1
            self._requested = None
            self._closed, self._wake = Event(), Event()
            thread = Thread(
                target=self._serve,
                args=(self._epoch, self._closed, self._wake),
                name="application-metadata",
                daemon=True,
            )
            thread.start()

    def request(self, application_ids: Iterable[str]) -> None:
        ids = tuple(sorted(set(application_ids)))
        with self._lock:
            if not self._started or ids == self._requested:
                return
            self._requested = ids
            self._generation += 1
            self._pending = self._generation, ids
            self._wake.set()

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            self._requested = None
            self._pending = None

    def stop(self) -> None:
        with self._lock:
            self._started = False
            self._epoch += 1
            self._pending = None
            self._closed.set()
            self._wake.set()
            for source in self._sources:
                self._remove_source(source)
            self._sources.clear()

    def _serve(self, epoch: int, closed: Event, wake: Event) -> None:
        while not closed.is_set():
            signaled = wake.wait(self._refresh_interval)
            wake.clear()
            if closed.is_set():
                return
            with self._lock:
                if closed.is_set() or epoch != self._epoch:
                    return
                if (
                    not signaled
                    and self._pending is None
                    and self._requested is not None
                ):
                    self._generation += 1
                    self._pending = self._generation, self._requested
                pending, self._pending = self._pending, None
            if pending is None:
                continue
            generation, ids = pending
            try:
                metadata = self._resolve(ids)
            except OSError as exc:
                log.debug("Could not read application metadata: %s", exc)
                continue
            self._publish(
                epoch,
                generation,
                replace(metadata, hidden_ids=metadata.hidden_ids & frozenset(ids)),
            )

    def _publish(
        self, epoch: int, generation: int, metadata: ApplicationMetadata
    ) -> None:
        with self._lock:
            if (
                not self._started
                or epoch != self._epoch
                or generation != self._generation
            ):
                return
            source = 0

            def deliver() -> bool:
                with self._lock:
                    self._sources.discard(source)
                    if (
                        not self._started
                        or epoch != self._epoch
                        or generation != self._generation
                    ):
                        return False
                    current = metadata
                    if (
                        current.applications is None
                        and self._snapshot.applications is not None
                    ):
                        current = replace(
                            current, applications=self._snapshot.applications
                        )
                    changed = current != self._snapshot
                    self._snapshot = current
                if changed:
                    self._on_changed()
                return False

            source = self._schedule_idle(deliver)
            self._sources.add(source)


def create_metadata_lookup(
    *,
    on_changed: Callable[[], None],
    schedule_idle: Callable[..., int],
    remove_source: Callable[[int], bool],
    registrations: ExecutableRegistrationBackend,
) -> ApplicationMetadataLookup | None:
    if not is_windows():
        return None
    from docking.platform.backends.windows.catalog import read_application_metadata

    return ApplicationMetadataLookup(
        resolve=lambda ids: read_application_metadata(
            ids, registrations=registrations.applications()
        ),
        on_changed=on_changed,
        schedule_idle=schedule_idle,
        remove_source=remove_source,
    )
