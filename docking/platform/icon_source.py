"""Optional application artwork as immutable pixels, independent of GTK."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.platform.applications.types import ApplicationInfo


@dataclass(frozen=True)
class IconPixels:
    """Tightly packed, straight-alpha RGBA pixels ready for presentation."""

    pixels: bytes
    width: int
    height: int


class ApplicationIconSource(ABC):
    """Nonblocking optional artwork with main-loop readiness notifications."""

    @abstractmethod
    def get(self, info: ApplicationInfo, size: int) -> IconPixels | None: ...

    @abstractmethod
    def stop(self) -> None: ...


ApplicationIconSourceFactory = Callable[
    [Callable[[str], None]], ApplicationIconSource | None
]
