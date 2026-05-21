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

from pathlib import Path


def ensure_dir(path: Path | str) -> Path:
    """Create a directory path and return it as a Path."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def ensure_parent_dir(path: Path | str) -> Path:
    """Create the parent directory for a file path and return the file path."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    return file_path
