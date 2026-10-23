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

"""Native Wayland backend package."""

from docking.platform.backends.wayland.cosmic_session import CosmicSessionBackend
from docking.platform.backends.wayland.portals import (
    WaylandPortalColorPickerService,
    XdgDesktopPortalColorPicker,
    load_portal_color_picker,
)
from docking.platform.backends.wayland.runtime import (
    ForeignToplevelProtocolAdapter,
    WaylandProtocolFactories,
    WaylandProtocolRuntime,
    WorkspaceProtocolAdapter,
    load_protocol_factories,
)
from docking.platform.backends.wayland.services import (
    WaylandLayerShellSurfaceService,
    layer_shell_is_supported,
    load_gtk_layer_shell,
)
from docking.platform.backends.wayland.session import WaylandLayerShellSessionBackend
from docking.platform.backends.wayland.toplevels import (
    WaylandForeignToplevelWindowService,
    load_foreign_toplevel_protocol,
)
from docking.platform.backends.wayland.workspaces import (
    WaylandWorkspaceService,
    load_workspace_protocol,
)

__all__ = [
    "CosmicSessionBackend",
    "ForeignToplevelProtocolAdapter",
    "WaylandForeignToplevelWindowService",
    "WaylandLayerShellSessionBackend",
    "WaylandLayerShellSurfaceService",
    "WaylandPortalColorPickerService",
    "WaylandProtocolFactories",
    "WaylandProtocolRuntime",
    "WaylandWorkspaceService",
    "WorkspaceProtocolAdapter",
    "XdgDesktopPortalColorPicker",
    "layer_shell_is_supported",
    "load_foreign_toplevel_protocol",
    "load_gtk_layer_shell",
    "load_portal_color_picker",
    "load_protocol_factories",
    "load_workspace_protocol",
]
