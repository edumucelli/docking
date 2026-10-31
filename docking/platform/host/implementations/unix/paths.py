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

"""Filesystem path helpers."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlparse


def local_path_from_uri_or_path(target: str) -> Path | None:
    """Decode a local file URI or expand a plain path safely."""
    if not target:
        return None
    try:
        parsed = urlparse(target)
        if parsed.scheme == "file":
            if not parsed.path.startswith("/") or parsed.netloc.casefold() not in {
                "",
                "localhost",
            }:
                return None
            path = Path(os.fsdecode(unquote_to_bytes(parsed.path)))
        elif not parsed.scheme:
            path = Path(target).expanduser()
        else:
            return None
    except (RuntimeError, ValueError):
        return None
    return None if "\0" in str(path) else path


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


HOST_XDG_DATA_DIRS = (
    "/var/lib/snapd/desktop",
    "/run/host/share",
    "/run/host/usr/local/share",
    "/run/host/usr/share",
    "/run/host/var/lib/flatpak/exports/share",
)
HOST_FILESYSTEM_ROOT = Path("/run/host")
