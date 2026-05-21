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

import os
from pathlib import Path


def xdg_config_home() -> Path:
    """Return XDG_CONFIG_HOME or its standard user default."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def xdg_cache_home() -> Path:
    """Return XDG_CACHE_HOME or its standard user default."""
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def xdg_data_home() -> Path:
    """Return XDG_DATA_HOME or its standard user default."""
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def xdg_state_home() -> Path:
    """Return XDG_STATE_HOME or its standard user default."""
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")


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
