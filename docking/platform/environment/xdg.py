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
import sys
from pathlib import Path


def _windows_folder(variable: str, fallback: str) -> Path:
    return Path(os.environ.get(variable) or Path.home() / "AppData" / fallback)


def xdg_config_home() -> Path:
    """Return XDG_CONFIG_HOME or its standard user default."""
    default = (
        _windows_folder("APPDATA", "Roaming")
        if sys.platform == "win32"
        else Path.home() / ".config"
    )
    return Path(os.environ.get("XDG_CONFIG_HOME") or default)


def xdg_cache_home() -> Path:
    """Return XDG_CACHE_HOME or its standard user default."""
    default = (
        _windows_folder("LOCALAPPDATA", "Local")
        if sys.platform == "win32"
        else Path.home() / ".cache"
    )
    return Path(os.environ.get("XDG_CACHE_HOME") or default)


def xdg_data_home() -> Path:
    """Return XDG_DATA_HOME or its standard user default."""
    default = (
        _windows_folder("APPDATA", "Roaming") / "Data"
        if sys.platform == "win32"
        else Path.home() / ".local" / "share"
    )
    return Path(os.environ.get("XDG_DATA_HOME") or default)


def xdg_state_home() -> Path:
    """Return XDG_STATE_HOME or its standard user default."""
    default = (
        _windows_folder("LOCALAPPDATA", "Local") / "State"
        if sys.platform == "win32"
        else Path.home() / ".local" / "state"
    )
    return Path(os.environ.get("XDG_STATE_HOME") or default)


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
