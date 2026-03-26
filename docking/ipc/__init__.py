"""D-Bus remote-control surface for Docking.

This package keeps transport concerns isolated from the rest of the dock.
Core/UI modules should continue to expose plain Python methods; the IPC layer
adapts those methods to a stable D-Bus contract.
"""

from docking.ipc.items_service import DockItemsService

__all__ = ["DockItemsService"]
