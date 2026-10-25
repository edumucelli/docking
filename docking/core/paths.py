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
from string import ascii_letters
from urllib.parse import unquote_to_bytes, urlsplit
from urllib.request import url2pathname


def is_explicit_path(target: str) -> bool:
    """Recognize path syntax for the current filesystem without accessing it."""
    if target.startswith(("/", "~/", "./", "../")) or target.lower().startswith(
        "file:"
    ):
        return True
    return os.name == "nt" and (
        target.startswith(("\\", "~\\", ".\\", "..\\")) or _has_drive(target)
    )


def _has_drive(target: str) -> bool:
    return len(target) >= 2 and target[0] in ascii_letters and target[1] == ":"


def local_path_from_uri_or_path(target: str) -> Path | None:
    """Decode an OS file URI or expand a plain path, preserving UNC authority.

    POSIX accepts only empty/localhost authorities. Windows maps other file
    authorities to UNC paths rather than silently opening a same-named local
    file. Drive paths must be recognized before URL parsing sees their colon.
    """
    if not target or "\0" in target:
        return None
    try:
        if os.name == "nt" and _has_drive(target):
            return Path(target).expanduser()
        parsed = urlsplit(target)
        if parsed.scheme == "file":
            if not parsed.path.startswith("/"):
                return None
            authority = parsed.netloc
            if authority.casefold() in {"", "localhost"}:
                authority = ""
            elif os.name != "nt" or any(char in authority for char in "@:\\%"):
                return None
            if os.name == "nt":
                url_path = f"//{authority}{parsed.path}" if authority else parsed.path
                path = Path(url2pathname(url_path))
                if not path.is_absolute():
                    return None
            else:
                path = Path(os.fsdecode(unquote_to_bytes(parsed.path)))
        elif not parsed.scheme:
            path = Path(target).expanduser()
        else:
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return None if "\0" in str(path) else path


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
