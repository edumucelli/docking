"""Compatibility imports for consumers of the former SNI-only service."""

from docking.platform.tray.service import POLL_INTERVAL_S, TrayService

StatusNotifierService = TrayService

__all__ = ["POLL_INTERVAL_S", "StatusNotifierService"]
