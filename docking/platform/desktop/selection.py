"""Composition entry point for the narrow desktop facade."""

from functools import cache

from .base import DesktopServices


@cache
def desktop_services() -> DesktopServices:
    from .implementations.unix.backend import UnixDesktopServices

    return UnixDesktopServices()
