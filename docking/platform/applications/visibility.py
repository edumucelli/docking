"""Optional platform visibility overrides, resolved outside the GTK thread.

Gio supplies the desktop-file visibility rules on Unix. Platforms that need
additional metadata provide the same immutable hidden-ID result to the registry.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from threading import Event, RLock, Thread

from docking.log import get_logger
from docking.platform.environment import is_windows

log = get_logger("application_visibility")


class ApplicationVisibilityLookup:
    """Coalesce metadata reads and reject callbacks from obsolete requests."""

    def __init__(
        self,
        *,
        resolve: Callable[[tuple[str, ...]], frozenset[str]],
        on_changed: Callable[[], None],
        schedule_idle: Callable[..., int],
        remove_source: Callable[[int], bool],
    ) -> None:
        self._resolve = resolve
        self._on_changed = on_changed
        self._schedule_idle = schedule_idle
        self._remove_source = remove_source
        self._lock = RLock()
        self._started = False
        self._epoch = 0
        self._generation = 0
        self._requested: tuple[str, ...] | None = None
        self._pending: tuple[int, tuple[str, ...]] | None = None
        self._closed = Event()
        self._wake = Event()
        self._sources: set[int] = set()
        self._hidden: frozenset[str] = frozenset()

    @property
    def hidden_ids(self) -> frozenset[str]:
        return self._hidden

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
                name="application-visibility",
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
            wake.wait()
            wake.clear()
            if closed.is_set():
                return
            with self._lock:
                if closed.is_set() or epoch != self._epoch:
                    return
                pending, self._pending = self._pending, None
            if pending is None:
                continue
            generation, ids = pending
            try:
                hidden = self._resolve(ids)
            except OSError as exc:
                log.debug("Could not read application visibility: %s", exc)
                hidden = frozenset()
            self._publish(epoch, generation, hidden & frozenset(ids))

    def _publish(self, epoch: int, generation: int, hidden: frozenset[str]) -> None:
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
                    changed = hidden != self._hidden
                    self._hidden = hidden
                if changed:
                    self._on_changed()
                return False

            source = self._schedule_idle(deliver)
            self._sources.add(source)


def create_visibility_lookup(
    *,
    on_changed: Callable[[], None],
    schedule_idle: Callable[..., int],
    remove_source: Callable[[int], bool],
) -> ApplicationVisibilityLookup | None:
    if not is_windows():
        return None
    from docking.platform.backends.windows.packages import hidden_application_ids

    return ApplicationVisibilityLookup(
        resolve=hidden_application_ids,
        on_changed=on_changed,
        schedule_idle=schedule_idle,
        remove_source=remove_source,
    )
