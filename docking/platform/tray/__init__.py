"""System tray contracts shared by platform implementations and presentation."""

from .backend import TrayBackend, create_backend
from .state import TrayIconPixmap, TrayItem, TrayState

__all__ = ["TrayBackend", "TrayIconPixmap", "TrayItem", "TrayState", "create_backend"]
