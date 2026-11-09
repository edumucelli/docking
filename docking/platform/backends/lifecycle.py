"""Own session services and unwind their dependencies after partial startup."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass

from docking.log import get_logger
from docking.platform.backends.base import Service

log = get_logger(name="session_lifecycle")


@dataclass(frozen=True)
class _Stage:
    name: str
    start: Callable[[], None] | None
    stop: Callable[[], None]


class ServiceLifecycle:
    """Start services in declaration order and attempt every owned cleanup.

    Cleanups belong to the session from registration, including services whose
    start has not run yet. This also supports stopping a constructed backend
    before startup. Cleanup-only stages own resources acquired in constructors.
    """

    def __init__(self) -> None:
        self._stages: list[_Stage] = []
        self._cleanup = ExitStack()
        self._started = False
        self._starting = False
        self._stopped = False

    def add(
        self,
        name: str,
        *,
        start: Callable[[], None] | None = None,
        stop: Callable[[], None],
    ) -> None:
        """Register a service, or just cleanup for an already acquired resource."""
        stage = _Stage(name=name, start=start, stop=stop)
        self._stages.append(stage)
        self._cleanup.callback(self._stop_stage, stage)

    def add_service(self, name: str, service: Callable[[], Service | None]) -> None:
        """Resolve the current service, including compositor bundle replacements."""

        def start() -> None:
            """Start the service currently selected by the session."""
            current = service()
            if current is not None:
                current.start()

        def stop() -> None:
            """Stop the current service after compositor bundle replacement."""
            current = service()
            if current is not None:
                current.stop()

        self.add(name, start=start, stop=stop)

    @staticmethod
    def _stop_stage(stage: _Stage) -> None:
        """Keep cleanup failures from replacing a startup error or escaping stop."""
        try:
            stage.stop()
        except BaseException:
            # Shutdown must finish even if a callback interrupts or exits.
            log.exception("Could not stop session service %s", stage.name)

    def start(self) -> None:
        """Unwind all owned services if any startup callback fails."""
        if self._started or self._starting:
            return
        if self._stopped:
            # Individual services retain their existing restart restrictions.
            # Rebuild in declaration order: newly registered stages may already
            # have queued callbacks from registration after the previous stop.
            self._cleanup.pop_all()
            for stage in self._stages:
                self._cleanup.callback(self._stop_stage, stage)
            self._stopped = False
        self._starting = True
        try:
            for stage in self._stages:
                if stage.start is not None:
                    try:
                        stage.start()
                    finally:
                        if self._stopped:
                            # A nested main loop can dispatch shutdown while a
                            # start callback is still acquiring resources.
                            self._stop_stage(stage)
                    if self._stopped:
                        return
            self._started = True
        except BaseException:
            self.stop()
            raise
        finally:
            self._starting = False

    def stop(self) -> None:
        """Log cleanup failures and continue, without raising during shutdown."""
        self._started = False
        self._stopped = True
        self._cleanup.close()
