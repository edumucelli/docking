"""Screenshot operations shared by Unix tool and native Windows backends."""

from abc import ABC, abstractmethod
from math import ceil, floor
from typing import Literal

from docking.platform.backends.base import Rect, WindowSnapshot

from .state import Mode, ScreenshotResult, SelectedRegion

SelectionPhase = Literal["press", "motion", "release"]


class ScreenshotBackend(ABC):
    select_region = False

    def prepare_capture(
        self, parent: object | None, *, source: WindowSnapshot | None = None
    ) -> None:
        """Supply the owned dock surface on GTK before background capture."""
        return

    def begin_selection(self, window: object) -> None:
        """Observe an owned region overlay on GTK when native input is needed."""
        return

    def selection_point(
        self, phase: SelectionPhase, x: float, y: float
    ) -> tuple[float, float] | None:
        """Return precise logical coordinates for an overlay input event."""
        return x, y

    def end_selection(self) -> None:
        """Remove any native observer before the overlay is destroyed."""
        return

    @abstractmethod
    def available(self) -> bool:
        """Discover capture support on a worker, without showing capture UI."""

    def prepare_region(self, region: SelectedRegion) -> Rect | None:
        """Prepare GTK logical root coordinates on GTK before worker capture."""
        return Rect(
            floor(region.x),
            floor(region.y),
            ceil(region.right) - floor(region.x),
            ceil(region.bottom) - floor(region.y),
        )

    @abstractmethod
    def capture(self, mode: Mode, *, region: Rect | None = None) -> ScreenshotResult:
        """Capture on a worker; region is the value from prepare_region()."""

    @abstractmethod
    def stop(self) -> None:
        """Cancel pending reads and release backend-owned resources."""
