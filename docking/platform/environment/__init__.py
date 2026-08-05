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

"""Desktop environment detection and DE-specific tweaks (public API).

Implementations live in:

- ``environment.py`` -- desktop detection, backend probing, apply_tweaks.
- ``xdg.py`` -- XDG Base Directory resolution.
- ``flatpak.py`` -- Flatpak host helpers.

This package re-exports the public surface so callers can stay on
``from docking.platform.environment import detect_desktop`` etc.
"""

from docking.platform.environment.environment import (
    Desktop,
    apply_tweaks,
    backend_name,
    compositor_active,
    detect_desktop,
    is_flatpak,
    is_gamescope_session,
    is_gnome_session,
    is_kde_session,
    is_mate_session,
    is_wayland_session,
    is_x11_backend,
    is_xwayland_session,
    log_runtime_snapshot,
)
from docking.platform.environment.xdg import (
    docking_cache_dir,
    docking_config_dir,
    docking_data_dir,
    docking_state_dir,
    xdg_cache_home,
    xdg_config_home,
    xdg_data_home,
    xdg_state_home,
)

__all__ = [
    "Desktop",
    "apply_tweaks",
    "backend_name",
    "compositor_active",
    "detect_desktop",
    "docking_cache_dir",
    "docking_config_dir",
    "docking_data_dir",
    "docking_state_dir",
    "is_flatpak",
    "is_gamescope_session",
    "is_gnome_session",
    "is_kde_session",
    "is_mate_session",
    "is_wayland_session",
    "is_x11_backend",
    "is_xwayland_session",
    "log_runtime_snapshot",
    "xdg_cache_home",
    "xdg_config_home",
    "xdg_data_home",
    "xdg_state_home",
]
