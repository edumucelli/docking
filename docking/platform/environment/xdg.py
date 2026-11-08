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

"""XDG Base Directory helpers."""

from __future__ import annotations

from pathlib import Path

from docking.platform.host.selection import host_services


def xdg_config_home() -> Path:
    return host_services().config_home()


def xdg_cache_home() -> Path:
    return host_services().cache_home()


def xdg_data_home() -> Path:
    return host_services().data_home()


def xdg_state_home() -> Path:
    return host_services().state_home()


def docking_config_dir() -> Path:
    """Return Docking's per-user configuration directory."""
    return xdg_config_home() / "docking"


def docking_cache_dir() -> Path:
    """Return Docking's per-user cache directory."""
    return xdg_cache_home() / "docking"


def docking_data_dir() -> Path:
    """Return Docking's per-user data directory."""
    return xdg_data_home() / "docking"


def docking_state_dir() -> Path:
    """Return Docking's per-user state directory."""
    return xdg_state_home() / "docking"
