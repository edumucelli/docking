"""Reduced backend for sessions without taskbar/window-manager powers."""

from docking.platform.backends.reduced.services import (
    ReducedPreviewService,
    ReducedSurfaceService,
    ReducedVisibilityService,
    ReducedWindowService,
)
from docking.platform.backends.reduced.session import (
    ReducedRuntimeServices,
    ReducedSessionBackend,
)

__all__ = [
    "ReducedPreviewService",
    "ReducedRuntimeServices",
    "ReducedSessionBackend",
    "ReducedSurfaceService",
    "ReducedVisibilityService",
    "ReducedWindowService",
]
