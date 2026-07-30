# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""D-Bus remote-control surface for Docking.

This package keeps transport concerns isolated from the rest of the dock.
Core/UI modules should continue to expose plain Python methods; the IPC layer
adapts those methods to a stable D-Bus contract.
"""

from docking.ipc.bus_host import DockBusHost
from docking.ipc.items_service import DockItemsService
from docking.ipc.search_service import DockSearchService

__all__ = ["DockBusHost", "DockItemsService", "DockSearchService"]
