"""Resolve native shared-library names without assuming Linux's ABI versions."""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from functools import lru_cache


@lru_cache(maxsize=32)
def _library_path(name: str, fallback: str, system: str) -> str:
    # Preserve Linux's existing packaged SONAMEs and avoid a subprocess on every
    # strut update. BSD assigns its own major/minor library versions.
    if system.startswith(("freebsd", "openbsd")):
        return ctypes.util.find_library(name) or fallback
    return fallback


def load_library(name: str, fallback: str) -> ctypes.CDLL:
    """Load an OS-resolved library, retaining the caller's established fallback."""
    return ctypes.cdll.LoadLibrary(_library_path(name, fallback, sys.platform))
