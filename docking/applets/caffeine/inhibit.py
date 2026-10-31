"""Wake-lock contract and the composite attempt-every-part policy."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Inhibitor(ABC):
    """Acquire/release contract for a wake lock."""

    @property
    @abstractmethod
    def active(self) -> bool: ...

    @abstractmethod
    def acquire(self) -> bool: ...

    @abstractmethod
    def release(self) -> None: ...


class CompositeInhibitor(Inhibitor):
    """Combine several inhibitors, acquiring/releasing all of them."""

    def __init__(self, parts: tuple[Inhibitor, ...]) -> None:
        self._parts = parts

    @property
    def active(self) -> bool:
        return any(part.active for part in self._parts)

    def acquire(self) -> bool:
        # Acquire every part (no short-circuit) so a failing one does not skip
        # the others; report success if any lock was taken.
        results = [part.acquire() for part in self._parts]
        return any(results)

    def release(self) -> None:
        for part in self._parts:
            part.release()
