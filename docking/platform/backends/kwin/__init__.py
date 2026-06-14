"""KWin / KDE Plasma 6 native Wayland backend package."""

from docking.platform.backends.kwin.atspi_window import AtspiWindowService
from docking.platform.backends.kwin.session import (
    KWinSessionBackend,
    KWinWorkspaceService,
)

__all__ = [
    "AtspiWindowService",
    "KWinSessionBackend",
    "KWinWorkspaceService",
]
